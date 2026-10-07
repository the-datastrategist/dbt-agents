.PHONY: install lint test build check

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

