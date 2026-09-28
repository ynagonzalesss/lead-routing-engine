.PHONY: install lint test validate eval eval-v1 eval-v2 all
PY ?= python3

install:
	$(PY) -m pip install -r requirements-dev.txt

lint:
	$(PY) -m ruff check src tests

test:
	$(PY) -m pytest

validate:
	PYTHONPATH=src $(PY) -m lead_engine.eval validate

# Live evaluation: calls the OpenAI API and costs money. Requires OPENAI_API_KEY.
eval:
	PYTHONPATH=src $(PY) -m lead_engine.eval run --version both

eval-v1:
	PYTHONPATH=src $(PY) -m lead_engine.eval run --version v1

eval-v2:
	PYTHONPATH=src $(PY) -m lead_engine.eval run --version v2

all: lint test validate
