# Sandbox image for running untrusted cloned-repo code.
# Usage: docker run --rm --network=none -v $REPO:/workspace:ro -w /workspace issuepilot-sandbox:latest
FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir pytest==8.2.2

WORKDIR /workspace

# Default: run the repo's pytest suite.
CMD ["sh", "-c", "pytest -v 2>&1"]
