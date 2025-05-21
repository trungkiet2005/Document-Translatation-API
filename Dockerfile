FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PORT=7860
ENV HF_ENDPOINT=https://hf-mirror.com
ENV XDG_CACHE_HOME=/tmp/.cache
ENV HF_HOME=/tmp/.cache/huggingface

# Install system dependencies and build tools
RUN apt-get update && \
    apt-get install --no-install-recommends -y \
    libgl1 libglib2.0-0 libxext6 libsm6 libxrender1 \
    build-essential pkg-config libzstd-dev \
    libcairo2-dev libjpeg-dev libfreetype6-dev && \
    rm -rf /var/lib/apt/lists/*

# Fix permission issues for any possible .cache locations
RUN mkdir -p /.cache && chmod -R 777 /.cache && \
    mkdir -p /root/.cache && chmod -R 777 /root/.cache && \
    mkdir -p /tmp/.cache/huggingface && chmod -R 777 /tmp/.cache

# Copy project files
COPY . .

# Install Python dependencies and the package
RUN uv pip install --system --no-cache . && \
    uv pip install --system --no-cache flask flask-cors gunicorn && \
    uv pip install --system --no-cache -U babeldoc "pymupdf<1.25.3" && \
    uv pip install --system --no-cache pdfminer.six numpy opencv-python-headless pikepdf

# Create temporary directory for file storage
RUN mkdir -p /tmp/pdf_translate_api && \
    chmod -R 777 /tmp/pdf_translate_api

# Command to run the Flask API
CMD ["python", "app.py"]
