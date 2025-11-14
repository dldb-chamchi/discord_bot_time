#!/bin/bash

# 1. 스크립트 실행 중 에러 나면 즉시 멈춤 (안전 장치)
set -e

echo "🔄 [1/4] 깃허브에서 최신 코드를 당겨옵니다..."
git pull origin main

# 2. .env 파일 확인 (없으면 큰일 나니까 체크)
if [ ! -f .env ]; then
    echo "❌ [오류] .env 파일이 없습니다! 봇을 실행할 수 없습니다."
    echo "💡 .env 파일을 만들고 토큰을 넣어주세요."
    exit 1
fi

echo "🐳 [2/4] 봇을 재조립(Build)하고 갈아끼웁니다..."
# --build: 코드 변경사항 적용을 위해 강제 재빌드
# -d: 백그라운드 실행
# --remove-orphans: 설정에서 삭제된 컨테이너 정리
docker compose up -d --build --remove-orphans

echo "🧹 [3/4] 서버 용량 확보를 위해 쓰레기 파일(구버전 이미지)을 청소합니다..."
docker image prune -f

echo "✅ [4/4] 재배포 완료! 봇이 정상 작동 중입니다."
echo "📝 로그를 확인하려면 'docker compose logs -f'를 입력하세요."