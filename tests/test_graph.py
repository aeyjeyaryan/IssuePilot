"""Tests for the LangGraph workflow and the node routing rules."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from issuepilot.config import settings
from issuepilot.graph import (
    build_graph,
    initial_state,
    route_after_patch,
    route_after_step,
    route_after_tests,
)
from issuepilot.llm import LLMClient
from issuepilot.runner import run_agent


def test_the_graph_has_all_the_nodes() -> None:
    graph = build_graph()
    nodes = set(graph.get_graph().nodes)

    for expected in (
        "fetch_issue",
        "prepare_repo",
        "index_codebase",
        "write_failing_test",
        "draft_fix",
        "apply_patch",
        "run_tests",
        "open_pr",
        "finalize",
    ):
        assert expected in nodes


def test_initial_state_contains_the_inputs() -> None:
    state = initial_state("https://github.com/a/b/issues/1", run_dir="/tmp/x", dry_run=True)

    assert state["issue_url"] == "https://github.com/a/b/issues/1"
    assert state["dry_run"] is True
    assert state["attempts"] == 0


# -- routing -----------------------------------------------------------------
def test_route_after_patch_runs_the_tests_when_the_patch_applied() -> None:
    assert route_after_patch({"patch_applied": True}) == "run_tests"


def test_route_after_patch_retries_when_the_patch_failed() -> None:
    assert route_after_patch({"patch_applied": False, "attempts": 1}) == "draft_fix"


def test_route_after_patch_gives_up_after_the_last_attempt() -> None:
    assert route_after_patch({"patch_applied": False, "attempts": 3}) == "finalize"


def test_route_after_patch_stops_on_a_fatal_error() -> None:
    assert route_after_patch({"fatal": True, "patch_applied": True}) == "finalize"


def test_route_after_tests_opens_the_pr_when_the_tests_pass() -> None:
    assert route_after_tests({"sandbox_ok": True}) == "open_pr"


def test_route_after_tests_retries_when_the_tests_fail() -> None:
    assert route_after_tests({"sandbox_ok": False, "attempts": 1}) == "draft_fix"


def test_route_after_tests_gives_up_after_the_last_attempt() -> None:
    assert route_after_tests({"sandbox_ok": False, "attempts": 3}) == "finalize"


def test_route_after_step_continues_unless_the_run_is_fatal() -> None:
    assert route_after_step({}) == "continue"
    assert route_after_step({"fatal": True}) == "finalize"


# -- end to end --------------------------------------------------------------
def test_demo_run_fixes_the_bug(tmp_path: Path) -> None:
    """The demo repository is fixed and the tests pass at the end."""
    demo_repo = settings.demo_root / "repo1"
    state = run_agent(
        issue_url=(demo_repo / "ISSUE.md").as_uri(),
        dry_run=True,
        demo_mode=True,
        demo_repo_path=str(demo_repo),
        demo_issue_path=str(demo_repo / "ISSUE.md"),
        run_dir=str(tmp_path / "run"),
        backend="reference",
    )

    assert state["outcome"] == "fixed", state.get("error") or state.get("sandbox_output")
    assert state["sandbox_ok"] is True
    assert state["patch"]
    assert "tests/test_issue_repro.py" in state["failing_test_path"]
    assert "calc.py" in state["diff"]
    assert state["report"]
    assert "Root cause" in state["report"]


def test_demo_run_never_touches_github(tmp_path: Path) -> None:
    demo_repo = settings.demo_root / "repo2"
    state = run_agent(
        issue_url=(demo_repo / "ISSUE.md").as_uri(),
        dry_run=True,
        demo_mode=True,
        demo_repo_path=str(demo_repo),
        demo_issue_path=str(demo_repo / "ISSUE.md"),
        run_dir=str(tmp_path / "run"),
        backend="reference",
    )

    assert state["outcome"] == "fixed", state.get("error")
    assert state["pr_created"] is False
    assert state["pr_url"] == ""


def test_demo_run_does_not_modify_the_bundled_repository(tmp_path: Path) -> None:
    demo_repo = settings.demo_root / "repo1"
    before = (demo_repo / "calc.py").read_text()

    run_agent(
        issue_url=(demo_repo / "ISSUE.md").as_uri(),
        dry_run=True,
        demo_mode=True,
        demo_repo_path=str(demo_repo),
        demo_issue_path=str(demo_repo / "ISSUE.md"),
        run_dir=str(tmp_path / "run"),
        backend="reference",
    )

    assert (demo_repo / "calc.py").read_text() == before


def test_the_working_copy_contains_the_new_test(tmp_path: Path) -> None:
    demo_repo = settings.demo_root / "repo1"
    state = run_agent(
        issue_url=(demo_repo / "ISSUE.md").as_uri(),
        dry_run=True,
        demo_mode=True,
        demo_repo_path=str(demo_repo),
        demo_issue_path=str(demo_repo / "ISSUE.md"),
        run_dir=str(tmp_path / "run"),
        backend="reference",
    )

    repo = Path(state["repo_path"])
    assert (repo / "tests" / "test_issue_repro.py").exists()
    assert (repo / "conftest.py").exists()
    assert "return a + b" in (repo / "calc.py").read_text()


def test_events_are_emitted_for_every_step(tmp_path: Path) -> None:
    from issuepilot.events import EventBus

    demo_repo = settings.demo_root / "repo1"
    bus = EventBus("test-run")
    run_agent(
        issue_url=(demo_repo / "ISSUE.md").as_uri(),
        dry_run=True,
        demo_mode=True,
        demo_repo_path=str(demo_repo),
        demo_issue_path=str(demo_repo / "ISSUE.md"),
        run_dir=str(tmp_path / "run"),
        bus=bus,
        backend="reference",
    )

    kinds = [event.kind for event in bus.history()]
    assert "log" in kinds
    assert "diff" in kinds
    assert "test_report" in kinds
    assert "report" in kinds
    steps = [e.data.get("step") for e in bus.history() if e.kind == "step_end"]
    assert "fetch_issue" in steps
    assert "finalize" in steps


def test_a_missing_demo_repository_is_reported(tmp_path: Path) -> None:
    state = run_agent(
        issue_url="demo://missing/issue/1",
        dry_run=True,
        demo_mode=True,
        demo_repo_path=str(tmp_path / "does-not-exist"),
        demo_issue_path=str(tmp_path / "does-not-exist" / "ISSUE.md"),
        run_dir=str(tmp_path / "run"),
        backend="reference",
    )

    assert state["outcome"] == "error"
    assert "does not exist" in state["error"]
