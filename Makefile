# ============================================================
# QueroLink — Makefile
# ============================================================

.PHONY: help install dev build test test-fast clean shell migrate seed lint build-css check

help: ## Mostra ajuda
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-18s\033[0m %s\n", $$1, $$2}'

install: ## Instala dependências Python
	python3 -m venv .venv
	.venv/bin/pip install -r requirements/local.txt 2>/dev/null || .venv/bin/pip install -r requirements/base.txt

dev: migrate ## Roda servidor de desenvolvimento
	.venv/bin/python manage.py runserver 0.0.0.0:8000

migrate: ## Aplica migrations
	.venv/bin/python manage.py makemigrations --noinput
	.venv/bin/python manage.py migrate --noinput

seed: ## Popula banco com 16 vendedores de demonstração
	.venv/bin/python -c "
import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','app.config.settings.local')
import django; django.setup()
exec(open('seed.py').read())
"

test: ## Roda todos os testes
	.venv/bin/python manage.py test app.apps.sellers app.apps.notifications app.apps.dashboard app.apps.api app.apps.commissions -v2

test-fast: ## Roda testes sem verbose
	.venv/bin/python manage.py test app.apps.sellers app.apps.notifications app.apps.dashboard app.apps.api app.apps.commissions

build: ## Build Docker (Tailwind + Python)
	docker compose build

up: ## Inicia containers
	docker compose up -d

down: ## Para containers
	docker compose down

logs: ## Logs dos containers
	docker compose logs -f --tail=100

shell: ## Abre Django shell
	.venv/bin/python manage.py shell

collectstatic: ## Coleta arquivos estáticos
	.venv/bin/python manage.py collectstatic --noinput

clean: ## Remove pycache e arquivos temporários
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf staticfiles media .pytest_cache

reset-db: ## Remove e recria banco SQLite
	rm -f db.sqlite3
	.venv/bin/python manage.py migrate --run-syncdb
	@echo "Banco resetado. Rode 'make seed' para popular dados de demo."

check: ## Verifica configuração Django
	.venv/bin/python manage.py check --deploy

urls: ## Lista todas as URLs registradas
	.venv/bin/python manage.py show_urls 2>/dev/null || echo "Instale django-extensions para show_urls"

build-css: ## Build Tailwind CSS
	npm run build:css

lint: ## Roda ruff linter
	.venv/bin/python -m ruff check app/

deploy-check: test ## Roda testes + check
	.venv/bin/python manage.py check --deploy
	@echo "Tudo OK para deploy."
