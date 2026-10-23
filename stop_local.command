#!/bin/bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
docker compose -p sentraai-dev-js down
echo '서비스를 종료했습니다. DB 볼륨은 보존했습니다.'
