# Stage 1: Build frontend
FROM node:22-alpine AS frontend-builder
WORKDIR /app
# Install deps FIRST (only invalidated when package files change), so a
# frontend source edit rebuilds just the fast vite step — npm ci (the slow
# network step) is cached until package.json / package-lock.json change.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
# Then copy the source and build (vite is fast; npm ci is the slow part).
COPY frontend/ ./
RUN npm run build

# Stage 2: Python backend
FROM python:3.12-slim-bookworm

# Install system deps (Docker CLI for sandbox, Tesseract OCR, ffmpeg for
# audio transcoding, texlive for .tex -> PDF compilation, Node.js 22+, and
# Chromium shared libraries for HyperFrames headless video rendering via
# Puppeteer)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    fonts-dejavu \
    fonts-liberation \
    fonts-noto \
    ffmpeg \
    tesseract-ocr \
    tesseract-ocr-eng \
    texlive-latex-base \
    texlive-latex-recommended \
    texlive-fonts-recommended \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libcairo2 \
    libcups2 \
    libdrm2 \
    libgbm1 \
    libnspr4 \
    libnss3 \
    libpango-1.0-0 \
    libx11-xcb1 \
    libxcb-dri3-0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxkbcommon0 \
    libxrandr2 \
    libxshmfence1 \
    unzip \
    && curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc \
    && chmod a+r /etc/apt/keyrings/docker.asc \
    && echo "deb [signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian bookworm stable" > /etc/apt/sources.list.d/docker.list \
    && apt-get update && apt-get install -y --no-install-recommends docker-ce-cli \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# texlive-latex-extra is ~700MB but the app's document template only needs
# the `listings` and `ulem` packages from it. Fetch just those two from CTAN
# into the local TeX tree, then verify by compiling the exact package set
# the app uses. If the compile fails for any reason, fall back to the full
# apt package so .tex -> PDF keeps working no matter what.
RUN if ! kpsewhich listings.sty >/dev/null 2>&1; then \
        mkdir -p /usr/local/share/texmf/tex/latex && cd /usr/local/share/texmf/tex/latex \
        && curl -fsSLo listings.zip https://mirrors.ctan.org/macros/latex/contrib/listings.zip \
        && unzip -qo listings.zip && rm -f listings.zip \
        && (cd listings && tex listings.ins >/dev/null 2>&1; rm -f *.dtx *.ins *.pdf *.log) \
        && curl -fsSLo ulem.zip https://mirrors.ctan.org/macros/latex/contrib/ulem.zip \
        && unzip -qo ulem.zip && rm -f ulem.zip \
        && mktexlsr /usr/local/share/texmf; \
    fi \
    && printf '%s\n' \
        '\documentclass{article}' \
        '\usepackage[utf8]{inputenc}' \
        '\usepackage[T1]{fontenc}' \
        '\usepackage{geometry}' \
        '\usepackage{graphicx}' \
        '\usepackage{booktabs}' \
        '\usepackage{xcolor}' \
        '\usepackage{listings}' \
        '\usepackage[normalem]{ulem}' \
        '\usepackage{hyperref}' \
        '\usepackage{amsmath}' \
        '\usepackage{amssymb}' \
        '\usepackage{longtable}' \
        '\usepackage{parskip}' \
        '\begin{document}' \
        'Test \uline{underline} and \lstinline|x = 1|.' \
        '\end{document}' > /tmp/tpl.tex \
    && { pdflatex -interaction=nonstopmode -halt-on-error -output-directory /tmp /tmp/tpl.tex >/dev/null 2>&1 && test -f /tmp/tpl.pdf; } \
    || { apt-get update && apt-get install -y --no-install-recommends texlive-latex-extra && rm -rf /var/lib/apt/lists/*; } \
    && rm -f /tmp/tpl.*

# Install HyperFrames CLI globally (includes Chromium via Puppeteer)
RUN npm install -g --no-audit --no-fund hyperframes && rm -rf ~/.npm && \
    rm -rf /usr/lib/node_modules/hyperframes/node_modules/onnxruntime-node && \
    rm -rf /usr/lib/node_modules/hyperframes/node_modules/onnxruntime-common

# Pre-download Chrome so the first user render skips the 107MB download
RUN mkdir -p /tmp/_hf_warmup && \
    echo '<!DOCTYPE html><html data-composition-id="w" data-start="0" data-width="320" data-height="240" data-duration="0.1"><body></body></html>' > /tmp/_hf_warmup/index.html && \
    echo '{"width":320,"height":240,"fps":24,"duration":0.1}' > /tmp/_hf_warmup/composition.json && \
    hyperframes render /tmp/_hf_warmup --output /tmp/_hf_warmup/out.mp4 2>/dev/null; \
    rm -rf /tmp/_hf_warmup

# The hyperframes CLI resolves its core runtime from /usr/lib/core/dist
# but npm installs it to /usr/lib/node_modules/hyperframes/dist.
# Create a compat symlink so the runtime manifest is found at build time.
RUN ln -sf /usr/lib/node_modules/hyperframes /usr/lib/core

# puppeteer-core 24.x moved its entry point from index.js to lib/cjs/...
# but the bundled hyperframes CLI still requires puppeteer-core/index.js.
RUN ln -sf lib/cjs/puppeteer/puppeteer-core.js /usr/lib/node_modules/hyperframes/node_modules/puppeteer-core/index.js

WORKDIR /app

# Copy backend
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ .

# Copy built frontend
COPY --from=frontend-builder /app/dist /app/static

# Expose port
EXPOSE 8000

# Volumes for persistence
VOLUME ["/app/data"]

# Run
ENV HF_HOME=/app/data/.cache/huggingface
ENV TRANSFORMERS_CACHE=/app/data/.cache/huggingface
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port 8000"]
