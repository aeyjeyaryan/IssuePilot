.PHONY: setup test demo sandbox-image lint clean run

setup:
	python -m pip install --upgrade pip
	pip install -r requirements.txt

test:
	pytest tests/ -v

demo:
	python -m issuepilot.cli demo

sandbox-image:
	docker build -t issuepilot-sandbox:latest -f Dockerfile .

run:
	python -m issuepilot.cli run $(URL) --dry-run

lint:
	python -m compileall -q issuepilot

clean:
	rm -rf chroma faiss.index .pytest_cache
	find . -name __pycache__ -type d -exec rm -rf {} +
