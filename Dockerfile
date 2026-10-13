FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    SAIBOGIRL_AUTOLOAD_SPEECH=1

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libgomp1 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/xiaoman
COPY requirements.txt ./
COPY deploy/requirements-cpu.txt ./deploy/
RUN pip install --no-cache-dir torch==2.6.0+cpu --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r deploy/requirements-cpu.txt

COPY app ./app
COPY web ./web
COPY vendor/s2s ./vendor/s2s
COPY vendor/musetalk_core ./vendor/musetalk_core
COPY config/models.json ./defaults/models.json
COPY deploy/entrypoint.py ./deploy/entrypoint.py
RUN useradd --uid 10001 --create-home xiaoman \
    && mkdir -p config logs models output/avatar \
    && chown -R 10001:10001 /opt/xiaoman
USER 10001:10001
EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7860/api/status', timeout=8).read()"
CMD ["python", "-m", "deploy.entrypoint"]
