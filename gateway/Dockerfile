# Gateway image: small, non-root, no build tools at runtime.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY gateway/requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt && useradd --uid 10001 --create-home gateway
COPY gateway /app/gateway
USER gateway
ENV GATEWAY_DATA_DIR=/data
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/health',timeout=4)"
CMD ["python", "-m", "gateway", "serve", "--port", "8080"]
