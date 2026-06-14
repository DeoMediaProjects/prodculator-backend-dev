SHELL := /bin/bash

VENV ?= venv
PYTHON ?= $(VENV)/bin/python
PIP ?= $(PYTHON) -m pip
UVICORN ?= $(PYTHON) -m uvicorn
ALEMBIC ?= $(PYTHON) -m alembic

APP ?= app.main:app
HOST ?= 0.0.0.0
PORT ?= 8001

.PHONY: help venv rebuild-venv install env run dev test test-verbose lint format check db-revision db-upgrade db-downgrade db-current db-history compose-up compose-down compose-logs clean

help: ## Show available commands
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "%-14s %s\n", $$1, $$2}'

venv: ## Create local virtualenv (Python 3.11+)
	@if [ -x "$(VENV)/bin/python" ]; then \
		$(VENV)/bin/python -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' || \
		( echo "Existing $(VENV) is not Python 3.11+. Run: make rebuild-venv" && exit 1 ); \
	else \
		if command -v pyenv >/dev/null 2>&1; then \
			PYENV_VERSION=3.11.14 pyenv exec python -m venv $(VENV); \
		elif command -v python3.11 >/dev/null 2>&1; then \
			python3.11 -m venv $(VENV); \
		else \
			echo "Python 3.11 is required. Install it, then run make rebuild-venv"; \
			exit 1; \
		fi; \
	fi

rebuild-venv: ## Recreate virtualenv using Python 3.11
	rm -rf $(VENV)
	$(MAKE) venv

install: venv ## Install project dependencies
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt

env: ## Create .env from .env.example if missing
	@test -f .env || cp .env.example .env

run: ## Run API (production mode)
	$(UVICORN) $(APP) --host $(HOST) --port $(PORT) --proxy-headers --forwarded-allow-ips "*"

dev: ## Run API with auto-reload
	$(UVICORN) $(APP) --reload --host $(HOST) --port $(PORT)

test: ## Run tests
	PYTHONPYCACHEPREFIX=/tmp/pycache $(PYTHON) -m pytest -q

test-verbose: ## Run tests (verbose)
	PYTHONPYCACHEPREFIX=/tmp/pycache $(PYTHON) -m pytest -vv

lint: ## Lint code with ruff
	$(PYTHON) -m ruff check app tests

format: ## Format code with ruff
	$(PYTHON) -m ruff format app tests

check: lint test ## Run lint + tests

db-revision: ## Create Alembic revision (usage: make db-revision MSG="init")
	@if [ -z "$(MSG)" ]; then echo 'MSG is required. Example: make db-revision MSG="init"'; exit 1; fi
	$(ALEMBIC) revision --autogenerate -m "$(MSG)"

db-upgrade: ## Apply DB migrations to head
	$(ALEMBIC) upgrade head

db-downgrade: ## Roll back one migration
	$(ALEMBIC) downgrade -1

db-current: ## Show current Alembic revision
	$(ALEMBIC) current

db-history: ## Show migration history
	$(ALEMBIC) history

compose-up: ## Start backend + postgres + redis via Docker Compose
	docker compose up -d --build

compose-down: ## Stop Docker Compose stack
	docker compose down

compose-logs: ## Tail Docker Compose logs
	docker compose logs -f --tail=200

clean: ## Remove caches and local artifacts
	find . -name "__pycache__" -type d -prune -exec rm -rf {} +
	find . -name "*.pyc" -delete
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov dist build .coverage .coverage.*
