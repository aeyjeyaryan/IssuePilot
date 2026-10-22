"""Sandboxed execution of untrusted repository code.

The safety model of IssuePilot is simple: code coming from a cloned repository
is **never** executed on the host.  It always runs inside a Docker container
that has

* no network (``--network=none``),
* a read-only root filesystem and a read-only bind mount of the repository,
* memory / CPU / PID limits,
* a hard wall-clock timeout.

If Docker is unavailable the runner refuses to execute anything for untrusted
repositories.  The only exception is the bundled demo (trusted, first-party
code shipped with IssuePilot) which may fall back to a restricted local
subprocess so the demo still works on a machine without Docker.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from issuepilot.config import settings

logger = logging.getLogger(__name__)


@dataclass
class SandboxResult:
    """Outcome of a sandboxed test run."""

    ok: bool
    exit_code: int = -1
    output: str = ""
    duration: float = 0.0
    backend: str = "docker"
    error: str = ""
    timed_out: bool = False
    tests_summary: str = ""
    failures: List[str] = field(default_factory=list)

    def __bool__(self) -> bool:  # pragma: no cover - convenience
        """Truthy when the tests passed."""
        return self.ok


class SandboxRunner:
    """Run pytest inside a constrained environment."""

    def __init__(self, image: Optional[str] = None, timeout: Optional[int] = None):
        """Configure the runner."""
        self.image = image or settings.sandbox_image
        self.timeout = timeout or settings.sandbox_timeout
        self.test_command = settings.sandbox_test_command

    # -- capability probing ------------------------------------------------
    @staticmethod
    def docker_available() -> bool:
        """Return ``True`` when the docker CLI is installed."""
        return shutil.which("docker") is not None

    @staticmethod
    def docker_usable() -> bool:
        """Return ``True`` when the docker daemon answers."""
        if not SandboxRunner.docker_available():
            return False
        try:
            result = subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=15)
            return result.returncode == 0
        except Exception:
            return False

    def build_image(self, context_dir: Optional[str] = None, dockerfile: Optional[str] = None) -> tuple[bool, str]:
        """Build the sandbox image.

        Args:
            context_dir: Build context (defaults to the project root).
            dockerfile: Path to the Dockerfile.

        Returns:
            ``(success, output)``.
        """
        context = Path(context_dir or settings.project_root)
        dockerfile_path = Path(dockerfile or context / "Dockerfile")
        if not SandboxRunner.docker_available():
            return False, "Docker is not installed"
        try:
            result = subprocess.run(
                ["docker", "build", "-t", self.image, "-f", str(dockerfile_path), str(context)],
                capture_output=True,
                text=True,
                timeout=900,
            )
            output = (result.stdout or "") + (result.stderr or "")
            return result.returncode == 0, output
        except Exception as exc:
            return False, f"Could not build the sandbox image: {exc}"

    # -- test running ------------------------------------------------------
    def run_tests(
        self,
        repo_path: str | Path,
        test_command: Optional[str] = None,
        allow_local_fallback: bool = False,
    ) -> SandboxResult:
        """Run the test suite of ``repo_path`` inside the sandbox.

        Args:
            repo_path: The repository checkout to test.
            test_command: Override for the command executed in the container.
            allow_local_fallback: Allow a restricted local subprocess when
                Docker is unavailable.  Only safe for trusted code.

        Returns:
            A :class:`SandboxResult` describing the run.
        """
        repo = Path(repo_path).resolve()
        command = test_command or self.test_command
        if not repo.is_dir():
            return SandboxResult(
                ok=False,
                error=f"Repository directory does not exist: {repo}",
                backend="none",
            )

        if SandboxRunner.docker_usable():
            return self._run_in_docker(repo, command)
        if allow_local_fallback:
            logger.warning(
                "Docker is unavailable; running the TRUSTED demo code in a restricted local subprocess",
            )
            return self._run_locally(repo, command, backend="local")
        return SandboxResult(
            ok=False,
            error=(
                "Docker is not available, so the repository code cannot be executed safely. "
                "Install Docker (https://docs.docker.com/get-docker/) or run `make sandbox-image` "
                "once Docker is available. Refusing to execute untrusted code on the host."
            ),
            backend="none",
        )

    def _run_in_docker(self, repo: Path, command: str) -> SandboxResult:
        """Execute the tests inside a locked down container."""
        docker_cmd = [
            "docker",
            "run",
            "--rm",
            "--network=none",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m",
            f"--memory={settings.sandbox_memory}",
            f"--cpus={settings.sandbox_cpus}",
            "--pids-limit=256",
            "-e",
            "PYTHONDONTWRITEBYTECODE=1",
            "-e",
            "HOME=/tmp",
            "-e",
            "PYTHONPATH=/workspace",
            "-v",
            f"{repo}:/workspace:ro",
            "-w",
            "/workspace",
            self.image,
            "sh",
            "-c",
            f"timeout {self.timeout} {command} 2>&1",
        ]
        started = time.time()
        try:
            result = subprocess.run(
                docker_cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout + 30,
            )
            output = (result.stdout or "") + (result.stderr or "")
            duration = time.time() - started
            return SandboxResult(
                ok=result.returncode == 0,
                exit_code=result.returncode,
                output=output,
                duration=duration,
                backend="docker",
                timed_out=result.returncode == 124,
            )
        except subprocess.TimeoutExpired:
            return SandboxResult(
                ok=False,
                exit_code=124,
                output=f"Tests timed out after {self.timeout}s",
                duration=time.time() - started,
                backend="docker",
                timed_out=True,
            )
        except Exception as exc:
            return SandboxResult(
                ok=False,
                error=f"Could not start the sandbox container: {exc}",
                duration=time.time() - started,
                backend="docker",
            )

    def _run_locally(self, repo: Path, command: str, backend: str = "local") -> SandboxResult:
        """Execute the tests in a restricted subprocess (trusted code only)."""
        started = time.time()
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONPATH"] = str(repo)
        env.pop("GITHUB_TOKEN", None)
        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=str(repo),
                env=env,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            output = (result.stdout or "") + (result.stderr or "")
            return SandboxResult(
                ok=result.returncode == 0,
                exit_code=result.returncode,
                output=output,
                duration=time.time() - started,
                backend=backend,
                timed_out=result.returncode == 124,
            )
        except subprocess.TimeoutExpired:
            return SandboxResult(
                ok=False,
                exit_code=124,
                output=f"Tests timed out after {self.timeout}s",
                duration=time.time() - started,
                backend=backend,
                timed_out=True,
            )
        except Exception as exc:
            return SandboxResult(
                ok=False,
                error=f"Could not run the tests: {exc}",
                duration=time.time() - started,
                backend=backend,
            )


def summarize_pytest(output: str) -> str:
    """Extract the one-line pytest summary from ``output``."""
    for line in reversed(output.splitlines()):
        stripped = line.strip()
        if "passed" in stripped or "failed" in stripped or "error" in stripped:
            if stripped.startswith(("=", "-")) or " in " in stripped:
                return stripped
    return ""


def collect_failures(output: str, limit: int = 10) -> List[str]:
    """Collect the failing test names from a pytest output."""
    failures: List[str] = []
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith(("FAILED ", "ERROR ")):
            identifier = stripped.split(" ", 1)[1].split(" ")[0].strip()
            failures.append(identifier.split("::")[-1] or identifier)
        if len(failures) >= limit:
            break
    return failures
