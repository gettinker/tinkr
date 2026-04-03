"""Read-only access to the monitored repository."""

from __future__ import annotations

import subprocess
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)


class RepoClient:
    """Thin wrapper around a local git repository for reading source files."""

    def __init__(self, repo_path: str) -> None:
        self._root = Path(repo_path).resolve()
        if not self._root.exists():
            raise FileNotFoundError(f"Repo path does not exist: {self._root}")

    def read_file(self, relative_path: str) -> str:
        path = self._root / relative_path.lstrip("/")
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return f"ERROR: File not found: {relative_path}"
        except UnicodeDecodeError:
            return f"ERROR: Cannot decode file (binary?): {relative_path}"

    def search(self, pattern: str, glob: str = "**/*.py", context_lines: int = 3) -> str:
        """Run ripgrep over the repo and return raw output."""
        result = subprocess.run(
            ["rg", "--glob", glob, f"--context={context_lines}", pattern, str(self._root)],
            capture_output=True,
            text=True,
        )
        return result.stdout or "(no matches)"

    def recent_commits(self, service_path: str = ".", n: int = 10) -> list[dict[str, str]]:
        """Return the last N commits touching a path."""
        result = subprocess.run(
            [
                "git", "-C", str(self._root),
                "log",
                f"-{n}",
                "--format=%H|%ae|%s|%ai",
                "--",
                service_path,
            ],
            capture_output=True,
            text=True,
        )
        commits = []
        for line in result.stdout.strip().splitlines():
            parts = line.split("|", 3)
            if len(parts) == 4:
                sha, author, subject, date = parts
                commits.append({"sha": sha, "author": author, "subject": subject, "date": date})
        return commits

    def blame(self, file_path: str, line_number: int) -> str:
        """Return git blame output for a specific line."""
        result = subprocess.run(
            [
                "git", "-C", str(self._root),
                "blame",
                "-L", f"{line_number},{line_number}",
                "--porcelain",
                file_path,
            ],
            capture_output=True,
            text=True,
        )
        return result.stdout
