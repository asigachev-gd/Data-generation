.PHONY: format lint test test-unit test-integration verify

format:
	python -m ruff format .

lint:
	python -m ruff format --check .
	python -m ruff check .

test: test-unit test-integration

test-unit:
	python -m pytest -m "not integration"

test-integration:
	python -m pytest -m integration

verify: lint test
