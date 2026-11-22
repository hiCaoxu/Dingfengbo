FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DINGFENGBO_HOST=0.0.0.0 \
    DINGFENGBO_PORT=8000 \
    DINGFENGBO_SERVER_DB=/data/cloud.db

WORKDIR /app

COPY requirements-server.txt .
# 依赖已锁定（requirements-server.txt）。构建机到 PyPI 的网络可能不稳：
# 索引可用 build-arg 切换（海外/国内镜像），并给够重试与超时。
ARG PIP_INDEX=https://pypi.org/simple
RUN pip install --no-cache-dir --retries 10 --timeout 120 \
    -i "$PIP_INDEX" -r requirements-server.txt

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
