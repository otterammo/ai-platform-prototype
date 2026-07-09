.PHONY: fmt lint typecheck test check

PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)

fmt:
	$(PYTHON) -m ruff check --fix .
	$(PYTHON) -m ruff format .

lint:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .

typecheck:
	$(PYTHON) -m mypy

test:
	$(PYTHON) -m pytest

check: lint typecheck test
