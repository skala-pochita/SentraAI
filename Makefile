COMPOSE := docker compose
BACKEND := cd backend &&
CONFIG ?= A_nano_single,B_nano_harness,C_large_single
REPEAT ?= 3

.PHONY: up down logs seed demo-reset test test-e2e lint eval fault-on fault-off env

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
	$(BACKEND) uv run ruff check . && uv run mypy core harness
	cd frontend && pnpm run lint

eval:
	$(COMPOSE) exec api python -m harness.eval.cli --configs $(CONFIG) --repeat $(REPEAT)

fault-on:
	@grep -q '^FAULT_INJECTION=' .env && sed -i.bak 's/^FAULT_INJECTION=.*/FAULT_INJECTION=github_rate_limit:$(or $(N),2)/' .env && rm -f .env.bak
	$(COMPOSE) up -d business-mcp

fault-off:
	@sed -i.bak 's/^FAULT_INJECTION=.*/FAULT_INJECTION=/' .env && rm -f .env.bak
	$(COMPOSE) up -d business-mcp
