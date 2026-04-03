"""Apply diffs to the local repo and open PRs on GitHub."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import structlog

from tinker.config import settings

log = structlog.get_logger(__name__)


class FixApplier:
    """Applies a unified diff to a local repo and opens a GitHub PR."""

    def __init__(self, repo_path: str) -> None:
        self._root = Path(repo_path).resolve()

    def validate(self, diff: str) -> list[str]:
        """Run Semgrep on the changed files. Returns a list of finding descriptions."""
        findings: list[str] = []
        with tempfile.TemporaryDirectory() as tmpdir:
            patch_file = Path(tmpdir) / "fix.patch"
            patch_file.write_text(diff)

            # Extract changed file paths from diff headers
            changed_files = [
                line[6:]  # strip "+++ b/"
                for line in diff.splitlines()
                if line.startswith("+++ b/")
            ]

            for rel_path in changed_files:
                full_path = self._root / rel_path
                if not full_path.exists():
                    continue
                result = subprocess.run(
                    ["semgrep", "--config=auto", "--json", str(full_path)],
                    capture_output=True,
                    text=True,
                )
                import json
                try:
                    data = json.loads(result.stdout)
                    for r in data.get("results", []):
                        severity = r.get("extra", {}).get("severity", "INFO")
                        msg = r.get("extra", {}).get("message", "")
                        findings.append(f"[{severity}] {rel_path}: {msg}")
                except json.JSONDecodeError:
                    pass

        return findings

    def apply_patch(self, diff: str) -> None:
        """Apply a unified diff to the working tree."""
        result = subprocess.run(
            ["git", "-C", str(self._root), "apply", "--check", "-"],
            input=diff,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise ValueError(f"Patch does not apply cleanly: {result.stderr}")

        subprocess.run(
            ["git", "-C", str(self._root), "apply", "-"],
            input=diff,
            check=True,
            text=True,
        )
        log.info("fix.patch_applied", repo=str(self._root))

    async def create_pr(
        self,
        diff: str,
        branch_name: str,
        title: str,
        body: str,
    ) -> str:
        """Apply diff, commit, push, and open a GitHub PR. Returns the PR URL."""
        import asyncio

        token = settings.github_token
        repo = settings.github_repo
        if not token or not repo:
            raise RuntimeError("GITHUB_TOKEN and GITHUB_REPO must be set to create PRs")

        # Validate diff with Semgrep before touching the repo
        findings = self.validate(diff)
        critical = [f for f in findings if "[CRITICAL]" in f or "[HIGH]" in f]
        if critical:
            raise ValueError(
                f"Semgrep found high-severity issues in the proposed fix:\n"
                + "\n".join(critical)
            )

        # Apply patch and commit
        self.apply_patch(diff)

        git = lambda *args: subprocess.run(  # noqa: E731
            ["git", "-C", str(self._root), *args], check=True, capture_output=True, text=True
        )
        git("checkout", "-b", branch_name)
        git("add", "-A")
        git("commit", "-m", title)

        # Push
        remote_url = f"https://x-access-token:{token.get_secret_value()}@github.com/{repo}.git"
        git("push", remote_url, f"HEAD:{branch_name}")

        # Create PR via GitHub API
        from github import Github

        gh = Github(token.get_secret_value())
        gh_repo = gh.get_repo(repo)
        pr = gh_repo.create_pull(
            title=title,
            body=body + "\n\n---\n_Opened by Tinker_",
            head=branch_name,
            base="main",
        )
        log.info("fix.pr_created", url=pr.html_url)
        return pr.html_url
