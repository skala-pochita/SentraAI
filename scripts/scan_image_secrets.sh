#!/bin/sh
# 배포 이미지에 실제 비밀값이 들어가지 않았는지 검사한다 (release 워크플로·make cli-images).
# Dockerfile 이 파일을 복사해 넣는 위치(/app, nginx 정적 파일)와 홈·설정 디렉터리에서
# - .env 파일 (예시 파일 제외)
# - 흔한 API 키 형식 (OpenAI·Anthropic·GitHub·AWS)
# 를 찾는다. 샘플 취약 코드(/app/targets)와 테스트(/app/backend/tests)의 가짜 키는 의도된 값이라 제외한다 (CLAUDE.md 규칙 8).
# 사용: scripts/scan_image_secrets.sh <이미지>
set -eu
image="$1"
found=$(docker run --rm --entrypoint sh "$image" -c '
  for root in /app /usr/share/nginx/html /root /home /workspace; do
    [ -d "$root" ] && find "$root" -type f 2>/dev/null
  done |
  while IFS= read -r f; do
    case "$f" in /app/targets/*|/app/tests/*|/app/backend/tests/*|*/.venv/*|*/site-packages/*|*/node_modules/*) continue ;; esac
    case "${f##*/}" in
      .env|.env.local|.env.production|.env.development) echo "env file: $f"; continue ;;
    esac
    if grep -IqE "(sk-(proj|ant|live)-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{30,}|AKIA[0-9A-Z]{16})" "$f" 2>/dev/null; then
      echo "key pattern: $f"
    fi
  done
')
if [ -n "$found" ]; then
  echo "✖ $image 에 비밀값으로 보이는 파일이 있습니다:" >&2
  echo "$found" >&2
  exit 1
fi
echo "✔ $image: 비밀값 없음"
