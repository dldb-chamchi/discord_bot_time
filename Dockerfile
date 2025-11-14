# Python 3.12이 깔려 있는 슬림한 리눅스 이미지 사용
FROM python:3.12-slim

# 컨테이너 안에서 작업할 디렉토리
WORKDIR /app

# 시스템 패키지 업데이트 (필요시 사용)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 파이썬 패키지 목록 먼저 복사
COPY requirements.txt .

# 파이썬 패키지 설치
RUN pip install --no-cache-dir -r requirements.txt

# 나머지 소스코드 전체 복사
COPY . .

# 컨테이너가 실행될 때 돌아갈 명령
# 이 레포 README에서 main.py를 실행하라고 되어 있어서 main.py를 엔트리로 사용합니다. :contentReference[oaicite:1]{index=1}
CMD ["python3", "main.py"]
