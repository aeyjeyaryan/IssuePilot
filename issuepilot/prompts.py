"""Prompt construction for the two LLM tasks: write a repro test, write a fix."""

from __future__ import annotations

from typing import Sequence, Tuple

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
You are given the issue, the failing test and the exact content of the source files.

Rules:
- Fix the ROOT CAUSE, not the symptom.
- Do NOT modify the test file, and do not weaken or delete any test.
- Do NOT add new dependencies.
- Answer with JSON only, in one of these two shapes:

  {"edits": [{"file": "relative/path.py", "old": "<exact snippet from the file>", "new": "<replacement>"}]}

  {"edits": [{"file": "relative/path.py", "function": "<name>", "new": "<full corrected function>"}]}

- Use the snippet form when a few lines change: `old` must be copied character
  for character from the file content below, including indentation, and must
  appear only once.  Keep `old` between 1 and 10 lines.
- Use the function form when the logic of a function has to be rewritten; `new`
  is then the complete new `def` block, correctly indented.
- `new` must be DIFFERENT from `old`: never repeat the buggy code.
- One edit per change. Do not rewrite whole files.
- Wrap the JSON in a single ```json fenced code block.
"""

REVISION_SYSTEM_PROMPT = """You are a senior Python engineer fixing a bug in an open source project.
Your previous attempt did not work.  You are given the issue, the failing test,
the current source files and the error output of the previous attempt.

Rules:
- Diagnose why the previous attempt failed and fix the real problem.
- Do NOT modify the test file, and do not weaken or delete any test.
- Answer with JSON only, either a snippet replacement:

  {"edits": [{"file": "relative/path.py", "old": "<exact snippet from the file>", "new": "<replacement>"}]}

  or a whole function replacement:

  {"edits": [{"file": "relative/path.py", "function": "<name>", "new": "<full corrected function>"}]}

- `old` must be copied character for character from the source files, including
  indentation, and must appear only once in that file.
- `new` must be DIFFERENT from `old`: never repeat the buggy code.
- Wrap the JSON in a single ```json fenced code block.
"""


def _format_context(chunks: Sequence[RetrievedChunk]) -> str:
    """Render the retrieved chunks for a prompt."""
    if not chunks:
        return "(no source code was retrieved)"
    blocks = []
    for retrieved in chunks:
        blocks.append(retrieved.format())
    return "\n\n".join(blocks)


def _format_files(files: Sequence[Tuple[str, str]]) -> str:
    """Render the exact content of the candidate files."""
    if not files:
        return "(no files were read)"
    blocks = []
    for path, content in files:
        blocks.append(f"--- {path} ---\n{content.rstrip()}\n")

    return "\n".join(blocks)


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
    files: Sequence[Tuple[str, str]] = (),
    repro_output: str = "",
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
    if repro_output:
        sections += [
            "",
            "Output of the reproduction test on the current (still broken) code:",
            "```",
            repro_output.strip()[-3000:],
            "```",
        ]
    if files:
        sections += ["", "Exact content of the files you may edit:", "```", _format_files(files), "```"]
    if previous_patch:
        sections += ["", "Your previous (failed) attempt was:", "```", previous_patch.strip(), "```"]
    if failure:
        sections += [
            "",
            "Output of the previous attempt (this is what still fails):",
            "```",
            failure.strip()[-4000:],
            "```",
        ]
    sections += ["", "Now produce the JSON edits that fix the bug."]
    return "\n".join(sections)
