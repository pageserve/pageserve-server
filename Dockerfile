FROM python:3.11-slim

WORKDIR /app

# System deps for PyMuPDF + curl for the healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmupdf-dev \
    mupdf-tools \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# PageIndex OSS source (cloned as-is, never modified)
COPY pageindex_src/ ./pageindex_src/

# App code
COPY app/         ./app/
COPY worker.py    .
COPY ui/          ./ui/
COPY migrations/  ./migrations/
COPY alembic.ini  .

# Data directories (mounted via volumes at runtime)
RUN mkdir -p /data/files /data/workspace /tmp/uploads

ENV PYTHONUNBUFFERED=1
ENV FILES_DIR=/data/files
ENV UPLOAD_DIR=/tmp/uploads

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD curl -f http://localhost:8000/health || exit 1

# workers=1 — async FastAPI does not need multiple workers; scale via replicas
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
