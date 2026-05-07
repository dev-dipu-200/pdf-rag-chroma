ARG PYTHON_BASE_IMAGE=python:3.12-slim
FROM ${PYTHON_BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY app ./app
COPY docker ./docker
COPY templates ./templates
COPY main.py ./main.py
COPY celery_worker.py ./celery_worker.py
COPY .env.docker ./.env.docker

RUN mkdir -p /app/uploads /app/chroma_data

EXPOSE 8000

CMD ["bash", "docker/api-entrypoint.sh"]
