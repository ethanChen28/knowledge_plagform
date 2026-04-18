FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends build-essential curl libreoffice fonts-wqy-microhei libgl1 libglib2.0-0 libsm6 libxext6 libxrender-dev && rm -rf /var/lib/apt/lists/*

COPY LightRAG /app/LightRAG
COPY RAG-Anything /app/RAG-Anything
COPY app /app/app
COPY config /app/config
COPY pyproject.toml /app/pyproject.toml
COPY README.md /app/README.md

RUN python -m pip install --upgrade pip && pip install -e '/app/LightRAG[api,offline-storage]' && pip install -e '/app/RAG-Anything[all]' && pip install -e /app

EXPOSE 8080

CMD multimodal-kb --config /app/config/config.yaml
