FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    pkg-config \
    libcairo2-dev \
    fonts-noto-cjk \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /app/skills/ppt-master /app/hosted_app

COPY requirements.txt /app/requirements.txt
COPY skills/ppt-master/requirements.txt /app/skills/ppt-master/requirements.txt
COPY hosted_app/requirements.txt /app/hosted_app/requirements.txt

RUN pip install -r requirements.txt \
    && pip install -r hosted_app/requirements.txt

COPY . /app

EXPOSE 7860

CMD ["python", "hosted_app/app.py"]
