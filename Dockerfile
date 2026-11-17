FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    SMARTGRADE_MODE=commercial SMARTGRADE_DATA_DIR=/var/lib/smartgrade \
    ALLOW_EXTERNAL_AI=false ALLOW_PAID_API=false OCR_PROVIDER=tesseract \
    OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 smartgrade \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin smartgrade \
    && mkdir -p /var/lib/smartgrade && chown 10001:10001 /var/lib/smartgrade
COPY requirements-runtime.lock ./
RUN --mount=type=secret,id=proxy_ca,required=false \
    if [ -f /run/secrets/proxy_ca ]; then \
      PIP_CERT=/run/secrets/proxy_ca python -m pip install --no-cache-dir -r requirements-runtime.lock; \
    else python -m pip install --no-cache-dir -r requirements-runtime.lock; fi
COPY app.py api.py config.py pyproject.toml ./
COPY modules ./modules
COPY database ./database
COPY scripts ./scripts
COPY assets ./assets
COPY .streamlit ./.streamlit
RUN find /app -type d -exec chmod 755 {} + \
    && find /app -type f -exec chmod 644 {} +
USER 10001:10001
EXPOSE 8501 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8501/_stcore/health',timeout=3)"
CMD ["python", "-m", "streamlit", "run", "app.py", "--server.address", "0.0.0.0", "--server.fileWatcherType", "none"]
