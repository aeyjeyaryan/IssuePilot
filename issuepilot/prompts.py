"""Prompt construction for the two LLM tasks: write a repro test, write a fix."""

from __future__ import annotations

from typing import Sequence

from issuepilot.retrieval import RetrievedChunk

TEST_SYSTEM_PROMPT = """You are a senior Python engineer working on a bug report.
Your job is to write a MINIMAL pytest test that reproduces the reported bug.

Rules:
- Output ONE self-contained pytest file and nothing else.
- The test must fail on the current (buggy) code and pass once the bug is fixed.
- Do not modify the source code, do not skip the test, do not use `pytest.skip`.
- Import the code under test using the module paths shown in the context.
- Keep the test deterministic; no network, no sleeps longer than a few milliseconds.
- Wrap the whole file in a single ```python fenced code block.
"""

PATCH_SYSTEM_PROMPT = """You are a senior Python engineer fixing a bug in an open source project.
You will be given the issue, the failing test and the relevant source code.

Rules:
- Fix the ROOT CAUSE, not the symptom.
- Do NOT modify the test file, and do not weaken or delete any test.
- Do NOT add new dependencies.
- Output ONLY a unified diff (git format) that can be applied with `git apply -p1`.
- Every changed file must be referenced with `diff --git a/<path> b/<path>`,
  `--- a/<path>` and `+++ b/<path>` headers, followed by `@@` hunks.
- Use paths relative to the repository root.
- Wrap the diff in a single ```diff fenced code block.
"""

REVISION_SYSTEM_PROMPT = """You are a senior Python engineer fixing a bug in an open source project.
Your previous patch did not work.  You are given the issue, the failing test,
the relevant source code and the error output of the previous attempt.

Rules:
- Diagnose why the previous attempt failed and fix the real problem.
- Do NOT modify the test file, and do not weaken or delete any test.
- Output ONLY a unified diff (git format) that can be applied with `git apply -p1`.
- Wrap the diff in a single ```diff fenced code block.
"""


def _format_context(chunks: Sequence[RetrievedChunk]) -> str:
    """Render the retrieved chunks for a prompt."""
    if not chunks:
        return "(no source code was retrieved)"
    blocks = []
    for retrieved in chunks:
        blocks.append(retrieved.format())
    return "\n\n".join(blocks)


def build_test_prompt(issue_title: str, issue_body: str, chunks: Sequence[RetrievedChunk]) -> str:
    """Build the prompt asking for a reproduction test."""
    return f"""Issue title: {issue_title}

Issue body:
{issue_body or "(the issue body was empty)"}

Relevant source code (retrieved from the repository):
{_format_context(chunks)}

Write the pytest file that reproduces this bug.
"""


def build_patch_prompt(
    issue_title: str,
    issue_body: str,
    test_code: str,
    chunks: Sequence[RetrievedChunk],
    failure: str = "",
    previous_patch: str = "",
) -> str:
    """Build the prompt asking for the fix."""
    sections = [
        f"Issue title: {issue_title}",
        "",
        "Issue body:",
        issue_body or "(the issue body was empty)",
        "",
        "Failing test written by the agent (must keep passing):",
        "```python",
        test_code.strip(),
        "```",
        "",
        "Relevant source code (retrieved from the repository):",
        _format_context(chunks),
    ]
    if previous_patch:
        sections += ["", "Your previous (failed) patch was:", "```diff", previous_patch.strip(), "```"]
    if failure:
        sections += [
            "",
            "Output of the previous attempt (this is what still fails):",
            "```",
            failure.strip()[-4000:],
            "```",
        ]
    sections += ["", "Now produce the unified diff that fixes the bug."]
    return "\n".join(sections)
