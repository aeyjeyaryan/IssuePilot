"""Tests for the sandbox runner."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from issuepilot.sandbox import SandboxRunner, collect_failures, summarize_pytest


def test_missing_repository_is_reported(tmp_path: Path) -> None:
    result = SandboxRunner().run_tests(tmp_path / "nope")

    assert not result.ok
    assert "does not exist" in result.error


def test_docker_command_is_locked_down(tmp_path: Path, monkeypatch) -> None:
    """The docker invocation must disable the network and cap the resources."""
    captured = {}

    class FakeCompleted:
        returncode = 0
        stdout = "1 passed"
        stderr = ""

    def fake_run(cmd, *args, **kwargs):
        captured["cmd"] = cmd
        return FakeCompleted()

    monkeypatch.setattr(SandboxRunner, "docker_usable", staticmethod(lambda: True))
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = SandboxRunner().run_tests(tmp_path)

    cmd = captured["cmd"]
    assert "--network=none" in cmd
    assert "--read-only" in cmd
    assert any(arg.startswith("--memory=") for arg in cmd)
    assert any(arg.startswith("--cpus=") for arg in cmd)
    assert any(arg.startswith("--pids-limit=") for arg in cmd)
    assert "timeout" in cmd[-1]
    assert result.ok


def test_docker_timeout_is_reported(tmp_path: Path, monkeypatch) -> None:
    def fake_run(cmd, *args, **kwargs):
        raise subprocess.TimeoutExpired(cmd, timeout=1)

    monkeypatch.setattr(SandboxRunner, "docker_usable", staticmethod(lambda: True))
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = SandboxRunner(timeout=1).run_tests(tmp_path)

    assert not result.ok
    assert result.timed_out


def test_without_docker_untrusted_code_is_refused(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(SandboxRunner, "docker_usable", staticmethod(lambda: False))

    result = SandboxRunner().run_tests(tmp_path)

    assert not result.ok
    assert "Docker" in result.error
    assert result.backend == "none"


def test_local_fallback_runs_the_tests(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(SandboxRunner, "docker_usable", staticmethod(lambda: False))
    (tmp_path / "test_ok.py").write_text("def test_ok():\n    assert True\n")

    result = SandboxRunner().run_tests(tmp_path, allow_local_fallback=True)

    assert result.backend == "local"
    assert result.ok, result.output


def test_local_fallback_reports_failing_tests(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(SandboxRunner, "docker_usable", staticmethod(lambda: False))
    (tmp_path / "test_bad.py").write_text("def test_bad():\n    assert 1 == 2\n")

    result = SandboxRunner().run_tests(tmp_path, allow_local_fallback=True)

    assert not result.ok
    assert "test_bad" in result.output


def test_docker_availability_probes_do_not_raise() -> None:
    assert isinstance(SandboxRunner.docker_available(), bool)
    assert isinstance(SandboxRunner.docker_usable(), bool)


def test_summarize_pytest_extracts_the_summary() -> None:
    output = "collected 3 items\n\nFAILED test_x.py::test_a - boom\n==== 1 failed, 2 passed in 0.12s ===="
    assert "1 failed" in summarize_pytest(output)


def test_collect_failures_lists_the_failing_tests() -> None:
    output = "FAILED tests/test_a.py::test_one - AssertionError\nFAILED tests/test_a.py::test_two - ValueError"
    failures = collect_failures(output)

    assert failures == ["test_one", "test_two"]
