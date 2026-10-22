"""Typer command line interface for IssuePilot."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import List, Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax

from issuepilot.config import settings
from issuepilot.events import EventBus
from issuepilot.llm import LLMClient
from issuepilot.logging_utils import setup_logging
from issuepilot.runner import run_agent
from issuepilot.sandbox import SandboxRunner

app = typer.Typer(
    add_completion=False,
    help="IssuePilot - turn a GitHub issue into a pull request.",
    no_args_is_help=True,
)
console = Console()
err_console = Console(stderr=True)

DEMO_REPOS = ["repo1", "repo2", "repo3"]


def _demo_paths(repo_name: str) -> tuple[Path, Path]:
    """Return the (repository, issue) paths of a demo issue."""
    root = settings.demo_root / repo_name
    return root, root / "ISSUE.md"


def _print_state(state: dict, show_diff: bool = True) -> None:
    """Print the outcome of a run."""
    outcome = state.get("outcome", "unknown")
    colour = "green" if outcome == "fixed" else ("yellow" if outcome == "gave-up" else "red")
    console.print()
    console.print(Panel(f"outcome: {outcome}", border_style=colour))
    if state.get("pr_url"):
        console.print(f"[green]Pull request:[/green] {state['pr_url']}")
    elif state.get("pr_message"):
        console.print(f"[yellow]{state['pr_message']}[/yellow]")
    if state.get("error"):
        console.print(f"[red]Error:[/red] {state['error']}")
    if state.get("test_summary"):
        console.print(f"[bold]Tests:[/bold] {state['test_summary']}")
    if show_diff and state.get("diff"):
        console.print()
        console.print("[bold cyan]Unified diff[/bold cyan]")
        console.print(Syntax(state["diff"], "diff", theme="monokai", line_numbers=False))
    if state.get("report"):
        report_path = Path(state.get("run_dir", ".")) / "report.md"
        try:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(state["report"], encoding="utf-8")
            console.print(f"[dim]Report written to {report_path}[/dim]")
        except OSError:
            pass


@app.command()
def run(
    issue_url: str = typer.Argument(..., help="URL of the GitHub issue to fix."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Do everything except open the pull request."),
    backend: str = typer.Option("auto", "--backend", help="LLM backend: auto, ollama or reference."),
    verbose: bool = typer.Option(False, "--verbose", help="Print debug logs."),
) -> None:
    """Fix a GitHub issue and open a pull request."""
    setup_logging("DEBUG" if verbose else settings.log_level, json_output=False)
    console.print(f"[bold green]IssuePilot[/bold green] working on [bold]{issue_url}[/bold]")
    if dry_run:
        console.print("[yellow]Dry run: the pull request will NOT be opened.[/yellow]")

    state = run_agent(issue_url=issue_url, dry_run=dry_run, backend=backend)
    _print_state(state)
    if state.get("outcome") != "fixed":
        raise typer.Exit(code=1)


@app.command()
def demo(
    repo: Optional[str] = typer.Option(None, "--repo", help="Run a single demo repository (repo1, repo2, repo3)."),
    backend: str = typer.Option("reference", "--backend", help="LLM backend: reference (offline) or ollama."),
    keep: bool = typer.Option(False, "--keep", help="Keep the working copies of the demo repositories."),
) -> None:
    """Run the whole pipeline on the bundled demo repositories (no GitHub needed)."""
    setup_logging(settings.log_level, json_output=False)
    names: List[str] = [repo] if repo else DEMO_REPOS
    unknown = [name for name in names if name not in DEMO_REPOS]
    if unknown:
        err_console.print(f"[red]Unknown demo repository: {', '.join(unknown)}[/red]")
        raise typer.Exit(code=2)

    failures = 0
    for name in names:
        repo_path, issue_path = _demo_paths(name)
        console.print()
        console.rule(f"demo: {name}")
        state = run_agent(
            issue_url=issue_path.as_uri(),
            dry_run=True,
            demo_mode=True,
            demo_repo_path=str(repo_path),
            demo_issue_path=str(issue_path),
            backend=backend,
        )
        _print_state(state)
        if state.get("outcome") != "fixed":
            failures += 1
        if not keep:
            import shutil

            # Only the working copy is removed; the report and the patch stay
            # available for inspection.
            run_dir = Path(state.get("run_dir", ""))
            for junk in (run_dir / "repo", run_dir / "repo.patch"):
                if junk.exists():
                    shutil.rmtree(junk, ignore_errors=True) if junk.is_dir() else junk.unlink()

    console.print()
    if failures:
        console.print(f"[red]{failures} of {len(names)} demo runs failed[/red]")
        raise typer.Exit(code=1)
    console.print(f"[green]All {len(names)} demo runs succeeded[/green]")


@app.command()
def doctor() -> None:
    """Check that the environment is ready (Docker, Ollama, GitHub access)."""
    setup_logging(settings.log_level, json_output=False)
    console.print("[bold]IssuePilot doctor[/bold]\n")

    docker_cli = SandboxRunner.docker_available()
    docker_ok = SandboxRunner.docker_usable()
    console.print(f"- Docker CLI: {'[green]found[/green]' if docker_cli else '[red]missing[/red]'}")
    console.print(f"- Docker daemon: {'[green]usable[/green]' if docker_ok else '[red]not usable[/red]'}")
    if not docker_ok:
        console.print("  [yellow]The sandbox cannot run code. Install Docker or use the demo (trusted code).[/yellow]")

    client = LLMClient()
    ollama_ok = client.available()
    console.print(f"- LLM backend: [bold]{client.describe()}[/bold] {'[green]ok[/green]' if ollama_ok else '[red]unavailable[/red]'}")
    if ollama_ok and "ollama" in client.describe():
        model = settings.ollama_model
        console.print(f"  [dim]model: {model}[/dim]")
        small = model.split(":")[0] in {"gemma3", "gemma2", "llama3.2", "phi3", "tinyllama", "qwen2.5"}
        if small:
            console.print(
                "  [yellow]Small models can reproduce the bug but often fail to fix it. "
                "A code capable model (qwen2.5-coder, codellama, ...) is recommended.[/yellow]"
            )

    from issuepilot.github_ops import gh_available

    console.print(f"- gh CLI: {'[green]found[/green]' if gh_available() else '[yellow]missing[/yellow]'}")
    console.print(f"- GITHUB_TOKEN: {'[green]set[/green]' if settings.github_token else '[yellow]not set[/yellow]'}")
    console.print(f"- Sandbox image: {settings.sandbox_image}")
    console.print(f"- Max attempts: {settings.max_attempts}")


@app.command("sandbox-build")
def sandbox_build() -> None:
    """Build the Docker image used to run the tests."""
    setup_logging(settings.log_level, json_output=False)
    runner = SandboxRunner()
    console.print(f"Building {settings.sandbox_image} ...")
    ok, output = runner.build_image()
    if ok:
        console.print("[green]Sandbox image built.[/green]")
    else:
        err_console.print("[red]Could not build the sandbox image:[/red]")
        err_console.print(output[-4000:])
        raise typer.Exit(code=1)


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Host to bind to."),
    port: int = typer.Option(8000, "--port", help="Port to bind to."),
    reload: bool = typer.Option(False, "--reload", help="Reload on code changes."),
) -> None:
    """Start the web UI and the HTTP API."""
    import uvicorn

    console.print(f"[bold green]IssuePilot[/bold green] serving on http://{host}:{port}")
    uvicorn.run("issuepilot.api:app", host=host, port=port, reload=reload)


@app.command()
def report(
    run_dir: str = typer.Argument(..., help="Directory of a previous run."),
) -> None:
    """Print the report of a previous run."""
    path = Path(run_dir) / "report.md"
    if not path.exists():
        err_console.print(f"[red]No report found at {path}[/red]")
        raise typer.Exit(code=1)
    console.print(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    app()
