FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

WORKDIR /app

# Use Chinese mirrors for apt (Tuna) and pip (Aliyun)
RUN sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null; \
    sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list 2>/dev/null; \
    echo "[global]\nindex-url = https://mirrors.aliyun.com/pypi/simple/\ntrusted-host = mirrors.aliyun.com" > /etc/pip.conf

RUN apt-get update && apt-get install -y --no-install-recommends build-essential curl libreoffice fonts-wqy-microhei libgl1 libglib2.0-0 libsm6 libxext6 libxrender-dev && rm -rf /var/lib/apt/lists/*

COPY LightRAG /app/LightRAG
COPY RAG-Anything /app/RAG-Anything
COPY app /app/app
COPY config /app/config
COPY pyproject.toml /app/pyproject.toml
COPY README.md /app/README.md

RUN python -m pip install --upgrade pip && pip install -e '/app/LightRAG[api,offline-storage]' && pip install -e '/app/RAG-Anything[all]' && pip install -e /app

# Patch MinerU VLM client: skip model name validation against /v1/models
# Some OpenAI-compatible servers use model aliases not listed in the models endpoint
RUN python -c "\
import mineru_vl_utils.vlm_client.http_client as hc; \
p = hc.__file__; \
src = open(p).read(); \
src = src.replace(\
    'def _check_model_name(self, base_url: str, model_name: str):',\
    'def _check_model_name(self, base_url: str, model_name: str):\n        return  # patched: skip model name validation'); \
open(p, 'w').write(src); \
print(f'Patched {p}')\
"

EXPOSE 8080

CMD multimodal-kb --config /app/config/config.yaml
