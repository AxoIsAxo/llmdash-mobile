# Stage 1: Build frontend
FROM node:22-alpine AS frontend-builder
WORKDIR /app
COPY frontend/package.json frontend/ ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: Python backend
FROM python:3.12-slim

# Install system deps (including Docker CLI for sandbox and Tesseract OCR)
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-dejavu \
    ca-certificates \
    curl \
    tesseract-ocr \
    tesseract-ocr-eng \
    tesseract-ocr-chi-sim \
    tesseract-ocr-jpn \
    tesseract-ocr-kor \
    tesseract-ocr-rus \
    tesseract-ocr-fra \
    tesseract-ocr-deu \
    tesseract-ocr-spa \
    tesseract-ocr-ara \
    ffmpeg \
    && curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc \
    && chmod a+r /etc/apt/keyrings/docker.asc \
    && echo "deb [signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian bookworm stable" > /etc/apt/sources.list.d/docker.list \
    && apt-get update && apt-get install -y --no-install-recommends docker-ce-cli \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy backend
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ .

# Copy built frontend
COPY --from=frontend-builder /app/dist /app/static

# Expose port
EXPOSE 8000

# Add pydantic-settings (not in requirements)
RUN pip install --no-cache-dir pydantic-settings

# Volumes for persistence
VOLUME ["/app/data"]

# Run
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port 8000"]
