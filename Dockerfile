ARG PYTHON_BASE_IMAGE=python:3.12-slim
FROM ${PYTHON_BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY app ./app
COPY docker ./docker
COPY main.py ./main.py
COPY .env.dev ./.env.dev
COPY .env.prod ./.env.prod

RUN mkdir -p /app/uploads

EXPOSE 8000

CMD ["bash", "docker/api-entrypoint.sh"]
