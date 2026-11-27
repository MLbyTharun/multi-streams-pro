FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SCENE_WORKER_HOST=0.0.0.0 \
    SCENE_WORKER_PORT=8090

WORKDIR /app

ARG APT_MAX_FUTURE_TIME=10
RUN apt-get -o Acquire::Max-FutureTime=${APT_MAX_FUTURE_TIME} update && \
    apt-get install -y --no-install-recommends \
      ffmpeg \
      libglib2.0-0 \
      libgl1 \
      libgomp1 && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.linux.txt ./
RUN python -m pip install --upgrade pip && \
    pip install -r requirements.linux.txt

COPY Check_Environment.py ./
COPY scene_config.py ./
COPY vision_api.py ./
COPY task_progress.py ./
COPY HyperReality_Final_Action_Review.py ./
COPY HyperReality_Qwen2B_Live_Webcam_Developer.py ./
COPY HyperReality_Qwen2B_Live_RTSP_Developer.py ./
COPY HyperReality_Qwen2B_Run_Good_Case_Actions.py ./
COPY scene_worker_manager.py ./
COPY livekit_scene_worker.py ./
COPY test_livekit_transport.py ./

RUN groupadd --system sceneworker && \
    useradd --system --gid sceneworker --create-home --home-dir /home/sceneworker sceneworker && \
    mkdir -p /app/outputs && \
    chown -R sceneworker:sceneworker /app /home/sceneworker

USER sceneworker

EXPOSE 8090

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import json,urllib.request; data=json.load(urllib.request.urlopen('http://127.0.0.1:8090/healthz', timeout=3)); raise SystemExit(0 if data.get('status')=='ok' else 1)"

CMD ["python", "-u", "scene_worker_manager.py"]
