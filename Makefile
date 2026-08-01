.PHONY: setup test init score fingerprint clean

VENV := .venv
PY := $(VENV)/bin/python

setup:
	python3 -m venv $(VENV)
	$(PY) -m pip install --quiet --upgrade pip
	$(PY) -m pip install --quiet -r requirements.txt
	@echo "done. copy .env.example to .env and fill it in."

test:
	PYTHONPATH=src $(PY) -m pytest tests -q

init:
	PYTHONPATH=src $(PY) -m receipts.cli init

# make score HANDLE=someone
score:
	PYTHONPATH=src $(PY) -m receipts.cli score $(HANDLE)

fingerprint:
	PYTHONPATH=src $(PY) -m receipts.cli fingerprint

clean:
	rm -rf $(VENV) .pytest_cache
	find . -name __pycache__ -type d -exec rm -rf {} +
