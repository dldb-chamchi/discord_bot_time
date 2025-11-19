# 1. 가볍고 안정적인 파이썬 3.10 버전 사용
FROM python:3.10-slim

# [추가] 파이썬 로그가 버퍼링 없이 바로 출력되게 설정 (중요!)
ENV PYTHONUNBUFFERED=1

# 2. 컨테이너 내 작업 폴더 설정
WORKDIR /app

# (나머지 내용은 그대로...)
RUN apt-get update && \
    apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "main.py"]
