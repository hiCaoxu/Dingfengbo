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

RUN mkdir -p /data

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=3)"

CMD ["python", "run_server.py"]
