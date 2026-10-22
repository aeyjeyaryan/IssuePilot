.PHONY: help setup test demo demo-live doctor sandbox-image run serve lint clean

PYTHON ?= python

help:
	@echo "IssuePilot"
	@echo ""
	@echo "  make setup          install the dependencies"
	@echo "  make test           run the test suite"
	@echo "  make demo           run the offline demo (deterministic, no model needed)"
	@echo "  make demo-live      run the demo with the local Ollama model"
	@echo "  make doctor         check Docker / Ollama / GitHub access"
	@echo "  make sandbox-image  build the Docker image used to run the tests"
	@echo "  make run URL=...    fix an issue (add DRY_RUN=1 to stop before the PR)"
	@echo "  make serve          start the web UI on http://127.0.0.1:8000"
	@echo "  make lint           byte-compile every module"
	@echo "  make clean          remove caches and run artefacts"

setup:
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r requirements.txt

test:
	$(PYTHON) -m pytest tests/ -q

demo:
	$(PYTHON) -m issuepilot.cli demo

demo-live:
	$(PYTHON) -m issuepilot.cli demo --backend ollama

doctor:
	$(PYTHON) -m issuepilot.cli doctor

sandbox-image:
	docker build -t issuepilot-sandbox:latest -f Dockerfile .

run:
	$(PYTHON) -m issuepilot.cli run $(URL) $(if $(DRY_RUN),--dry-run,)

serve:
	$(PYTHON) -m issuepilot.cli serve

lint:
	$(PYTHON) -m compileall -q issuepilot tests

clean:
	rm -rf .issuepilot .pytest_cache
	find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
