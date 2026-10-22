"""Tests for the GitHub helpers (no network access is performed)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from issuepilot.github_ops import GitHubError, IssueInfo, parse_issue_url


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://github.com/owner/repo/issues/42", ("owner", "repo", 42)),
        ("http://github.com/owner/repo/issues/7/", ("owner", "repo", 7)),
        ("https://github.com/owner/repo/issues/1234#issuecomment-1", ("owner", "repo", 1234)),
        ("  https://github.com/Owner-Name/repo.js/issues/9  ", ("Owner-Name", "repo.js", 9)),
    ],
)
def test_parse_issue_url(url: str, expected: tuple) -> None:
    assert parse_issue_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "",
        "not a url",
        "https://github.com/owner/repo",
        "https://github.com/owner/repo/pull/42",
        "https://gitlab.com/owner/repo/issues/42",
    ],
)
def test_parse_issue_url_rejects_other_urls(url: str) -> None:
    with pytest.raises(GitHubError):
        parse_issue_url(url)


def test_the_error_message_shows_the_expected_format() -> None:
    with pytest.raises(GitHubError) as error:
        parse_issue_url("https://github.com/owner/repo")
    assert "issues/123" in str(error.value)


def test_issue_info_exposes_the_clone_url() -> None:
    info = IssueInfo(number=1, title="Bug", body="Details", url="u", owner="owner", repo="repo")
    assert info.clone_url == "https://github.com/owner/repo.git"
    assert "Bug" in info.text
    assert "Details" in info.text


def test_cloning_without_git_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    from issuepilot import github_ops

    monkeypatch.setattr(github_ops.shutil, "which", lambda name: None)
    with pytest.raises(GitHubError):
        github_ops.GitHubOps().clone_repository("https://example.com/x.git", "/tmp/does-not-matter")


def test_a_failing_clone_is_reported(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from issuepilot import github_ops

    monkeypatch.setattr(github_ops.shutil, "which", lambda name: "/usr/bin/git")

    def fake_run(cmd, cwd=None, timeout=120):
        return subprocess.CompletedProcess(cmd, 128, "", "fatal: repository not found")

    monkeypatch.setattr(github_ops, "_run", fake_run)
    with pytest.raises(GitHubError) as error:
        github_ops.GitHubOps().clone_repository("https://example.com/x.git", tmp_path / "clone")
    assert "repository not found" in str(error.value)


def test_a_dry_run_pull_request_does_not_push(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from issuepilot import github_ops

    calls: list = []

    def fake_run(cmd, cwd=None, timeout=120):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "done", "")

    monkeypatch.setattr(github_ops, "_run", fake_run)
    monkeypatch.setattr(github_ops.shutil, "which", lambda name: "/usr/bin/gh")

    ok, message = github_ops.GitHubOps().open_pull_request(
        tmp_path,
        owner="owner",
        repo="repo",
        branch="issuepilot/fix-1",
        title="Fix",
        body="Body",
        dry_run=True,
    )
    assert ok is True
    assert not any("push" in " ".join(cmd) for cmd in calls)
