COMPOSE := docker compose
BACKEND := cd backend &&
CONFIG ?= A_nano_single,B_nano_harness,C_large_single
REPEAT ?= 3
CODE_CONFIGS ?= A_nano_single,B_nano_harness,C_large_single

.PHONY: up down logs seed demo-reset test test-e2e lint eval eval-code fault-on fault-off env

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

SET ?= final
CONFIGS ?= A,B
REPS ?= 1

# Agent·Cloud 감사 A/B 비교 (LLM 키 없으면 미측정으로 기록). CONFIGS=rules 는 LLM 없는 결정론 자체 점검.
eval:
	$(BACKEND) uv run python -m core.cloud.eval_runner --set $(SET) --configs $(CONFIGS) --reps $(REPS)

eval-code:  ## 코드·Agent 감사 A/B/C 비교 (명세 17장). 예: make eval-code CODE_CONFIGS=A_nano_single,B_nano_harness REPEAT=1
	$(COMPOSE) exec eval-worker python -m harness.eval.cli --configs $(CODE_CONFIGS) --repeat $(REPEAT)

eval-selfcheck:
	$(BACKEND) uv run python -m core.cloud.eval_runner --set all --configs rules

fault-on:
	@grep -q '^FAULT_INJECTION=' .env && sed -i.bak 's/^FAULT_INJECTION=.*/FAULT_INJECTION=github_rate_limit:$(or $(N),2)/' .env && rm -f .env.bak
	$(COMPOSE) up -d business-mcp

fault-off:
	@sed -i.bak 's/^FAULT_INJECTION=.*/FAULT_INJECTION=/' .env && rm -f .env.bak
	$(COMPOSE) up -d business-mcp
