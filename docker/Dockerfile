FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DINGFENGBO_HOST=0.0.0.0 \
    DINGFENGBO_PORT=8000 \
    DINGFENGBO_SERVER_DB=/data/cloud.db

WORKDIR /app

COPY requirements-server.txt .
RUN pip install --no-cache-dir -r requirements-server.txt

COPY app ./app
COPY server ./server
COPY run_server.py ./run_server.py

# 以非 root 用户运行（固定 uid 便于宿主数据目录授权），缩小容器被攻破后的影响面。
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /data \
    && chown -R appuser:appuser /app /data

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=3)"

CMD ["python", "run_server.py"]
