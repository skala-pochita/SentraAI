COMPOSE := docker compose
BACKEND := cd backend &&
REPEAT ?= 3
CODE_CONFIGS ?= A_nano_single,B_nano_harness

SENTRA_REGISTRY ?= ghcr.io/skala-pochita
SENTRA_VERSION ?= $(shell node -p "require('./cli/package.json').version")

.PHONY: cli-images cli-pack up down logs seed demo-reset test test-e2e lint eval eval-code fault-on fault-off env

env:
	@test -f .env || cp .env.example .env

up: env
	$(COMPOSE) up -d --build --wait

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=200

seed:
	$(COMPOSE) exec api python -m api.seed

demo-reset:
	$(COMPOSE) run --rm --no-deps -v $(CURDIR)/targets:/app/targets:ro business-mcp python -m mcp_servers.business.demo_reset
	$(COMPOSE) restart target-agent

test:
	$(BACKEND) uv run pytest

test-e2e:
	$(BACKEND) uv run pytest -m "llm or infra" tests/e2e tests/integration

lint:
	$(BACKEND) uv run ruff check . && uv run mypy core harness mcp_servers
	cd frontend && pnpm run lint

# 코드·Agent 감사 A/B/C 비교 실험 (명세 17장). 예: make eval CODE_CONFIGS=A_nano_single,B_nano_harness REPEAT=1
eval:
	$(COMPOSE) exec eval-worker python -m harness.eval.cli --configs $(CODE_CONFIGS) --repeat $(REPEAT)

eval-code: eval

fault-on:
	@grep -q '^FAULT_INJECTION=' .env && sed -i.bak 's/^FAULT_INJECTION=.*/FAULT_INJECTION=rate_limit:$(or $(N),2)/' .env && rm -f .env.bak
	$(COMPOSE) up -d business-mcp

fault-off:
	@sed -i.bak 's/^FAULT_INJECTION=.*/FAULT_INJECTION=/' .env && rm -f .env.bak
	$(COMPOSE) up -d business-mcp

# sentraai CLI 용 이미지를 이 컴퓨터에 빌드한다 (push 없음). 배포는 v* 태그 → .github/workflows/release.yml
cli-images:
	docker build -f backend/Dockerfile -t $(SENTRA_REGISTRY)/sentraai-backend:$(SENTRA_VERSION) .
	docker build -f targets/target-agent/Dockerfile -t $(SENTRA_REGISTRY)/sentraai-target-agent:$(SENTRA_VERSION) .
	docker build -t $(SENTRA_REGISTRY)/sentraai-frontend:$(SENTRA_VERSION) frontend
	for s in backend target-agent frontend; do sh scripts/scan_image_secrets.sh $(SENTRA_REGISTRY)/sentraai-$$s:$(SENTRA_VERSION) || exit 1; done

cli-pack:
	cd cli && npm test && npm pack
