FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Seoul

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata curl && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/state /app/logs /app/data_cache

# `yes` 자동 입력 (실거래 시작 확인 프롬프트)
CMD ["bash", "-c", "echo yes | python -u main.py"]
