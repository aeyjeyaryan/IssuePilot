"""Regenerate the reference patches of the demo repositories.

The fixed version of every demo module is kept next to the patch so the
reference patches can always be rebuilt with ``git diff``.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FIXES = {
    "repo1": {
        "calc.py": ("    return a - b\n", "    return a + b\n"),
    },
    "repo2": {
        "strings.py": (
            '    return text[:max_length] + "..."\n',
            '    if max_length <= 3:\n        return "." * max_length\n'
            '    return text[: max_length - 3] + "..."\n',
        ),
    },
    "repo3": {
        "lists.py": (
            "    return [items[i : i + size] for i in range(0, len(items) - size + 1, size)]\n",
            "    return [items[i : i + size] for i in range(0, len(items), size)]\n",
        ),
    },
}


def main() -> int:
    """Regenerate every reference patch."""
    for repo_name, files in FIXES.items():
        source = ROOT / "demo" / repo_name
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / repo_name
            shutil.copytree(source, work, ignore=shutil.ignore_patterns("_reference"))
            subprocess.run(["git", "-C", str(work), "init", "-q"], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(work), "add", "-A"], check=True, capture_output=True)
            subprocess.run(
                ["git", "-C", str(work), "-c", "user.email=a@b.c", "-c", "user.name=t",
                 "commit", "-q", "-m", "base"],
                check=True, capture_output=True,
            )
            for filename, (old, new) in files.items():
                path = work / filename
                text = path.read_text()
                if old not in text:
                    print(f"!! {repo_name}/{filename}: the buggy line was not found", file=sys.stderr)
                    return 1
                path.write_text(text.replace(old, new))
            diff = subprocess.run(
                ["git", "-C", str(work), "diff"], capture_output=True, text=True, check=True
            ).stdout
        target = source / "_reference" / "solution.patch"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(diff)
        print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
