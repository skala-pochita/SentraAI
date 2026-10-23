#!/bin/bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
trap 'echo "실행을 중단했습니다. 위 오류와 LOCAL_RUN.md를 확인해 주세요."; read -r -p "Enter를 누르면 닫습니다. "' ERR
command -v docker >/dev/null || { echo 'Docker Desktop을 설치하고 실행해 주세요.'; exit 1; }
docker info >/dev/null
docker compose version
if [ ! -f .env ]; then
  python3 - <<'PY'
from pathlib import Path
import secrets
template = Path('.env.example').read_text()
values = {'JWT_SECRET': secrets.token_hex(32), 'MCP_SERVICE_TOKEN': secrets.token_hex(24)}
lines = [f"{line.split('=', 1)[0]}={values[line.split('=', 1)[0]]}" if line.split('=', 1)[0] in values else line for line in template.splitlines()]
Path('.env').write_text('\n'.join(lines) + '\n')
Path('.env').chmod(0o600)
PY
fi
# Dedicated Compose project keeps its volumes separate from an existing install.
docker compose -p sentraai-dev-js build
docker compose -p sentraai-dev-js run --rm --no-deps business-mcp sh -c \
  'if [ ! -d /workspace/vuln-spring-app ] && [ ! -d /workspace/agent-config ]; then python -m mcp_servers.business.demo_reset; fi'
docker compose -p sentraai-dev-js up -d --wait
docker compose -p sentraai-dev-js exec -T api python -m api.seed
docker compose -p sentraai-dev-js exec -T rag-mcp python -m core.compliance_cli index
echo 'SentraAI: http://localhost:5173'
echo '데모 계정: admin@demo.io / Admin!demo1234 (.env에서 변경 가능)'
open 'http://localhost:5173'
read -r -p '서비스는 계속 실행됩니다. Enter를 누르면 이 창만 닫습니다. '
