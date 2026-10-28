SHELL := /bin/bash
PY := .venv/bin/python
PIP := .venv/bin/pip
UVICORN := .venv/bin/uvicorn

.PHONY: setup run test cov lint type fmt check docker-build docker-up clean

setup: ## Cria o venv e instala dependências de desenvolvimento
	python3 -m venv .venv
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements-dev.txt

run: ## Sobe a API em modo desenvolvimento
	$(UVICORN) app.main:create_app --factory --reload --host 0.0.0.0 --port 8000

test: ## Executa os testes
	$(PY) -m pytest

cov: ## Testes com relatório de cobertura
	$(PY) -m pytest --cov=app --cov-report=term-missing --cov-report=xml

lint: ## Ruff (lint + formatação)
	.venv/bin/ruff check .
	.venv/bin/ruff format --check .

fmt: ## Corrige lint e formata
	.venv/bin/ruff check --fix .
	.venv/bin/ruff format .

type: ## Tipagem estática
	.venv/bin/mypy app

check: lint type test ## Pipeline local completo

docker-build: ## Constrói a imagem
	docker build -t nexa-api .

docker-up: ## Sobe API + Ollama (perfil)
	docker compose up --build

clean: ## Remove artefatos locais
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage coverage.xml
