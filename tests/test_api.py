"""Tests for the FastAPI backend and the SSE stream."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from issuepilot.api import app
from issuepilot.config import settings


@pytest.fixture()
def client() -> TestClient:
    """Return a test client for the API."""
    return TestClient(app)


def test_health_endpoint(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_the_web_ui_is_served(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "IssuePilot" in response.text


def test_config_endpoint_exposes_no_secrets(client: TestClient) -> None:
    payload = client.get("/api/config").json()
    assert "max_attempts" in payload
    assert not any("token" in key.lower() for key in payload)


def test_an_unknown_run_is_a_404(client: TestClient) -> None:
    response = client.get("/api/runs/does-not-exist")
    assert response.status_code == 404


def test_starting_a_demo_run_returns_a_run_id(client: TestClient) -> None:
    response = client.post(
        "/api/runs",
        json={"issue_url": "", "demo": True, "demo_repo": "repo1", "dry_run": True},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["run_id"]
    assert payload["dry_run"] is True


def test_an_unknown_demo_repository_is_rejected(client: TestClient) -> None:
    response = client.post("/api/runs", json={"issue_url": "", "demo": True, "demo_repo": "nope"})
    assert response.status_code == 400


def test_the_event_stream_reports_every_step(client: TestClient) -> None:
    created = client.post(
        "/api/runs",
        json={"issue_url": "", "demo": True, "demo_repo": "repo1", "dry_run": True, "backend": "reference"},
    ).json()
    run_id = created["run_id"]

    events = []
    with client.stream("GET", f"/api/runs/{run_id}/events") as response:
        assert response.status_code == 200
        for line in response.iter_lines():
            if line.startswith("data:"):
                events.append(json.loads(line[5:].strip()))
            if len(events) > 60:
                break
            time.sleep(0.05)

    kinds = {event["kind"] for event in events}
    assert "log" in kinds
    assert "step_end" in kinds
    assert "diff" in kinds
    assert "test_report" in kinds


def test_the_run_snapshot_is_available(client: TestClient) -> None:
    created = client.post(
        "/api/runs",
        json={"issue_url": "", "demo": True, "demo_repo": "repo1", "dry_run": True, "backend": "reference"},
    ).json()
    run_id = created["run_id"]

    deadline = time.time() + 60
    payload = {}
    while time.time() < deadline:
        payload = client.get(f"/api/runs/{run_id}").json()
        if payload.get("finished"):
            break
        time.sleep(0.2)

    assert payload["finished"] is True
    assert payload["outcome"] == "fixed"
    assert payload["state"]["patch"]
