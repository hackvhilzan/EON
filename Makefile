# Makefile — EON Kernel
# Uso: make <target>

.PHONY: install dev-install test test-cov lint format typecheck clean run console docker-build docker-run smoke-test help

PYTHON := python3
PIP := pip

help: ## Mostrar esta ayuda
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

install: ## Instalar EON en modo producción
	$(PIP) install .

dev-install: ## Instalar EON + dependencias de desarrollo
	$(PIP) install -e ".[dev,llm]"

test: ## Ejecutar tests
	$(PYTHON) -m pytest

test-cov: ## Ejecutar tests con cobertura
	$(PYTHON) -m pytest --cov=eon --cov-report=term-missing --cov-report=html

test-quick: ## Ejecutar tests rápidos (excluir slow/integration/e2e)
	$(PYTHON) -m pytest -m "not slow and not integration and not e2e"

lint: ## Lint con ruff
	$(PYTHON) -m ruff check eon/

format: ## Formatear código con ruff
	$(PYTHON) -m ruff format eon/ && $(PYTHON) -m ruff check --fix eon/

typecheck: ## Type check con mypy
	$(PYTHON) -m mypy eon/ --ignore-missing-imports

check: lint typecheck test ## lint + typecheck + test (CI completo local)

run: ## Ejecutar una objetivo rápido desde CLI
	$(PYTHON) -m eon --descripcion "Hola Mundo" --criterio "Termina sin error"

console: ## Levantar la consola HTTP
	$(PYTHON) -m eon.console --host 127.0.0.1 --port 8765

smoke-test: ## Smoke test end-to-end
	$(PYTHON) scripts/smoke_test.py

docker-build: ## Construir imagen Docker
	docker build -t eon-console:latest .

docker-run: ## Ejecutar contenedor Docker
	docker-compose up

clean: ## Limpiar artefactos de build y cache
	rm -rf __pycache__ .pytest_cache .mypy_cache .ruff_cache htmlcov .eon_runtime
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
