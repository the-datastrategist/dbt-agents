.PHONY: install lint test build check plugin-check plugin-install

install:
	python3 -m pip install -e '.[all,dev]'

lint:
	python3 -m ruff check src tests
	python3 -m ruff format --check src tests

test:
	python3 -m pytest -q

build:
	uv build

check: lint test build

plugin-check:
	PYTHONPATH=src python3 -m pytest -q tests/test_plugin_package.py

plugin-install: plugin-check
	codex plugin marketplace add "$(CURDIR)"
	codex plugin add dbt-agents@thedatastrategist-internal
