# =============================================================================
# NSA Webservice — multi-stage Docker image
#
# Mirrors the Render build (pyproject + requirements.txt + playwright chromium
# + the WeasyPrint / OCR system libs). Multi-stage so the final image carries
# no build toolchain.
# =============================================================================

# ── syntax ───────────────────────────────────────────────────────────────────
# syntax=docker.io/docker/dockerfile:1

# ── Base: system libraries required by WeasyPrint, PyMuPDF, pdf2image, pytesseract
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app

SHELL ["/bin/bash", "-c"]

# WeasyPrint needs: libpango, libpangocairo, libcairo2, libgdk-pixbuf, libgobject
# PyMuPDF / reportlab / openpyxl need libGL / libGLib — covered by the set below.
# pdf2image needs poppler-utils (pdftoppm).
# pytesseract needs tesseract-ocr binary.
# psycopg2 needs libpq-dev (runtime: libpq5).
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
        g++ \
        libpq-dev \
        libpq5 \
        libcairo2 \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        libgdk-pixbuf-2.0-0 \
        libgobject-2.0-0 \
        libffi-dev \
        poppler-utils \
        tesseract-ocr \
        curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Builder: compile pure-Python + native wheels into /root/.local ────────────
FROM base AS builder
COPY pyproject.toml requirements.txt* ./

# Install the project (editable) + declared + dev deps. Doing this in the
# builder means the final layer only ships runtime artifacts, not compilers.
RUN pip install --user --no-cache-dir -e . && \
    pip install --user --no-cache-dir -r requirements.txt

# Playwright (used by the OCR pipeline) + its chromium browser. || true so a
# browser-download hiccup never breaks the image build (OCR degrades gracefully).
RUN python -m playwright install chromium || true

# ── Runtime ───────────────────────────────────────────────────────────────────
FROM base AS runtime

ENV PATH=/root/.local/bin:${PATH}

# Copy compiled/site-packages from the builder (no build tools).
COPY --from=builder /root/.local /root/.local

# Copy application source.
COPY . .

# Ensure the instance folder (SQLite fallback / uploads) is writable.
RUN mkdir -p /app/instance /app/uploads /app/logs && chmod -R 777 /app/instance /app/uploads

# Migrations are applied explicitly at deploy time (see render.yaml startCommand
# and the gunicorn entrypoint below), so no `flask db upgrade` here.
EXPOSE 8000

# gunicorn: 2 sync workers is fine for the Flask app; heavy PDF/OCR work is
# offloaded to the Celery worker (separate docker-compose service).
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status==200 else 1)"

CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "60", "app:app"]
