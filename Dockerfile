# Stage 1: Build frontend
FROM node:22-alpine AS frontend-builder
WORKDIR /app
COPY frontend/package.json frontend/ ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: Python backend
FROM python:3.12-slim

# Install system deps (Docker CLI for sandbox, Tesseract OCR, ffmpeg for
# audio transcoding, Node.js 22+, and Chromium shared libraries for
# HyperFrames headless video rendering via Puppeteer)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    fonts-dejavu \
    fonts-liberation \
    fonts-noto \
    ffmpeg \
    tesseract-ocr \
    libasound2t64 \
    libatk-bridge2.0-0t64 \
    libatk1.0-0t64 \
    libcairo2 \
    libcups2t64 \
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
    tesseract-ocr-eng \
    tesseract-ocr-chi-sim \
    tesseract-ocr-jpn \
    tesseract-ocr-kor \
    tesseract-ocr-rus \
    tesseract-ocr-fra \
    tesseract-ocr-deu \
    tesseract-ocr-spa \
    tesseract-ocr-ara \
    && curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc \
    && chmod a+r /etc/apt/keyrings/docker.asc \
    && echo "deb [signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian bookworm stable" > /etc/apt/sources.list.d/docker.list \
    && apt-get update && apt-get install -y --no-install-recommends docker-ce-cli \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install HyperFrames CLI globally (includes Chromium via Puppeteer)
RUN npm install -g hyperframes && rm -rf ~/.npm && \
    rm -rf /usr/lib/node_modules/hyperframes/node_modules/onnxruntime-node && \
    rm -rf /usr/lib/node_modules/hyperframes/node_modules/onnxruntime-common

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
