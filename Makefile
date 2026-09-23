SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.ONESHELL:
.DEFAULT_GOAL := help

UV ?= uv
BACKEND_PORT ?= 8000
FRONTEND_PORT ?= 3000
AGENT_SERVER_PORT ?= 2025
API_URL ?= http://127.0.0.1:$(BACKEND_PORT)
COMPOSE_ENV := $(if $(wildcard .env.local),--env-file .env.local,)
COMPOSE ?= docker compose $(COMPOSE_ENV)
STANDARDS_DIR := standards/idta-submodel-templates
STANDARDS_COMMIT := a9664731a903b29ac5f45e23ab3a25c581f3d92f

.PHONY: help install crawl-setup refs refs-check backend frontend worker dev studio extract lint format \
	typecheck test build check docker-build up down logs monitor smoke

help: ## Show the available commands.
	@awk 'BEGIN {FS = ":.*## "; print "MIA DPP commands\n"} /^[a-zA-Z0-9_.-]+:.*## / {printf "  %-14s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

refs: ## Initialize the pinned official IDTA template submodule.
	git submodule update --init --recursive $(STANDARDS_DIR)

refs-check:
	@test -f "$(STANDARDS_DIR)/README.md" || { \
		printf 'Missing standards checkout. Run: make refs\n' >&2; exit 1; \
	}
	actual="$$(git -C "$(STANDARDS_DIR)" rev-parse HEAD)"
	test "$$actual" = "$(STANDARDS_COMMIT)" || { \
		printf 'Expected IDTA templates at %s, found %s\n' "$(STANDARDS_COMMIT)" "$$actual" >&2; \
		exit 1; \
	}

install: refs ## Install the locked Python and frontend dependencies.
	$(UV) sync --project backend --locked --group dev
	npm ci

crawl-setup: ## Install the Chromium runtime used by Crawl4AI website imports.
	$(UV) run --project backend --no-sync python -m playwright install --only-shell chromium

backend: ## Run the Python API at http://127.0.0.1:8000.
	MIA_LOCAL_MODE=1 $(UV) run --project backend --no-sync uvicorn mia_dpp.main:app \
		--reload --host 127.0.0.1 --port $(BACKEND_PORT)

frontend: ## Run only the Next.js interface.
	VERCEL_ENV= MIA_LOCAL_MODE=1 \
	MIA_RESEARCH_DISPATCH=local \
	MIA_BACKEND_URL="$(API_URL)" \
	NEXT_PUBLIC_MIA_API_URL="$(API_URL)" \
		npm run dev -- --hostname 127.0.0.1 --port $(FRONTEND_PORT)

worker: ## Run the persistent local crawl/research worker.
	VERCEL_ENV= MIA_LOCAL_MODE=1 \
		$(UV) run --project backend --no-sync python -m mia_dpp.worker

dev: ## Run backend, local worker, and Next.js frontend together.
	@VERCEL_ENV= MIA_LOCAL_MODE=1 $(UV) run --project backend --no-sync uvicorn mia_dpp.main:app \
		--host 127.0.0.1 --port $(BACKEND_PORT) &
	backend_pid=$!
	VERCEL_ENV= MIA_LOCAL_MODE=1 $(UV) run --project backend --no-sync python -m mia_dpp.worker &
	worker_pid=$!
	trap 'kill "$backend_pid" "$worker_pid" 2>/dev/null || true; wait "$backend_pid" "$worker_pid" 2>/dev/null || true' EXIT INT TERM
	MIA_LOCAL_MODE=1 MIA_RESEARCH_DISPATCH=local \
	MIA_BACKEND_URL="$(API_URL)" \
	NEXT_PUBLIC_MIA_API_URL="$(API_URL)" \
		npm run dev -- --hostname 127.0.0.1 --port $(FRONTEND_PORT)

# Keep the diagnostic extraction path reproducible without starting either application server.
extract: ## Extract one URL; pass URL=..., OUTPUT=..., and optionally LLM=1.
	@test -n "$(URL)" || { printf 'Usage: make extract URL=https://... [OUTPUT=...] [LLM=1]\n' >&2; exit 2; }
	args=()
	# LLM extraction is opt-in so maintainers can inspect Crawl4AI-only behavior independently.
	if [[ "$(LLM)" = "1" ]]; then args+=(--llm); fi
	$(UV) run --project backend --no-sync python backend/scripts/extract_url.py \
		"$(URL)" --output "$(or $(OUTPUT),test-output/extraction)" "$${args[@]}"

lint: ## Check Python and TypeScript style and Python formatting.
	cd backend
	$(UV) run --project . --no-sync ruff check src tests
	$(UV) run --project . --no-sync ruff format --check src tests
	cd ..
	npm run lint

format: ## Format Python source and tests.
	cd backend
	$(UV) run --project . --no-sync ruff format src tests

typecheck: ## Run strict Python and TypeScript type checking.
	cd backend
	$(UV) run --project . --no-sync mypy
	cd ..
	./node_modules/.bin/tsc --noEmit

test: refs-check ## Run the deterministic Python test suite.
	cd backend
	PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 $(UV) run --project . --no-sync pytest \
		-p pytest_asyncio.plugin

build: ## Build the production frontend bundle.
	NEXT_PUBLIC_MIA_API_URL="$(API_URL)" npm run build

check: refs-check lint typecheck test build ## Run the complete local quality gate.
	$(COMPOSE) config --quiet

docker-build: ## Build the self-contained backend and frontend images.
	NEXT_PUBLIC_MIA_API_URL="$(API_URL)" $(COMPOSE) build

up: ## Start the production containers and wait for health checks.
	NEXT_PUBLIC_MIA_API_URL="$(API_URL)" $(COMPOSE) up -d --build --wait

down: ## Stop the local containers without deleting durable MIA data.
	$(COMPOSE) down

logs: ## Follow frontend, backend, and worker logs together.
	$(COMPOSE) logs -f frontend backend worker

monitor: ## Open frontend, backend, worker, and combined logs in a tmux grid.
	bash scripts/dev-tmux.sh

studio: ## Start the local Agent Server and print its LangGraph Studio link.
	$(COMPOSE) up -d --build --wait agent-server
	printf 'Open LangGraph Studio: https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:%s\n' "$(AGENT_SERVER_PORT)"

smoke: ## Start, probe, and always stop the production containers.
	@trap '$(COMPOSE) down' EXIT
	NEXT_PUBLIC_MIA_API_URL="$(API_URL)" $(COMPOSE) up -d --build --wait
	curl --fail --silent --show-error "$(API_URL)/health" >/dev/null
	curl --fail --silent --show-error "http://127.0.0.1:$(FRONTEND_PORT)/" >/dev/null
