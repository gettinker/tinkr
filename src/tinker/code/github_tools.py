"""GitHub code provider — tools for the fix agent to read and write code.

Used by POST /api/v1/fix (read: get_file, search_code, get_commits)
and POST /api/v1/approve (write: create_branch, update_files, create_pr).

Requires GITHUB_TOKEN and GITHUB_REPO in config.
"""

from __future__ import annotations

import difflib
import structlog

log = structlog.get_logger(__name__)


class GitHubCodeProvider:
    """Wraps PyGitHub — all repo operations go through here.

    Repo resolution order for a given service:
      1. GITHUB_REPOS JSON map  {"payments-api": "acme/payments", ...}
      2. GITHUB_REPO            single fallback repo
      3. RuntimeError           neither is configured
    """

    def __init__(self, service: str | None = None) -> None:
        import json
        from github import Github
        from tinker.config import settings

        if not settings.github_token:
            raise RuntimeError(
                "GITHUB_TOKEN is not configured. "
                "Set it in ~/.tinker/.env or run `tinker init server`."
            )

        token = settings.github_token.get_secret_value()
        self._gh = Github(token)

        # Resolve which repo to use
        repo_name: str | None = None
        if service:
            try:
                repos_map: dict[str, str] = json.loads(settings.github_repos)
                repo_name = repos_map.get(service)
            except Exception:
                pass

        if not repo_name:
            repo_name = settings.github_repo

        if not repo_name:
            raise RuntimeError(
                "No GitHub repository configured for this service. "
                "Set GITHUB_REPO (single repo) or GITHUB_REPOS (service map) "
                "in ~/.tinker/.env or run `tinker init server`."
            )

        self._repo = self._gh.get_repo(repo_name)
        log.info("github.provider_ready", repo=repo_name, service=service)

    # ── Read tools (used in fix agent loop) ──────────────────────────────────

    def get_file(self, path: str, ref: str | None = None) -> str:
        """Return the text content of a file at the given ref (default: default branch)."""
        try:
            kwargs: dict = {}
            if ref:
                kwargs["ref"] = ref
            content = self._repo.get_contents(path, **kwargs)
            if isinstance(content, list):
                return f"(directory listing)\n" + "\n".join(c.path for c in content)
            return content.decoded_content.decode("utf-8", errors="replace")
        except Exception as exc:
            return f"(error reading {path}: {exc})"

    def search_code(self, query: str, max_results: int = 10) -> str:
        """Search the repo's code using GitHub code search."""
        try:
            results = self._gh.search_code(f"{query} repo:{self._repo.full_name}")
            lines: list[str] = []
            for item in results[:max_results]:
                lines.append(f"{item.path}")
                # Include a short snippet from text_matches if available
                for match in getattr(item, "text_matches", [])[:1]:
                    fragment = match.get("fragment", "").replace("\n", " ")[:200]
                    if fragment:
                        lines.append(f"  …{fragment}…")
            return "\n".join(lines) if lines else "(no results)"
        except Exception as exc:
            return f"(search error: {exc})"

    def get_commits(self, path: str = ".", n: int = 10) -> str:
        """Return recent commits touching a path."""
        try:
            commits = self._repo.get_commits(path=path if path != "." else None)
            lines: list[str] = []
            for c in list(commits)[:n]:
                sha = c.sha[:8]
                date = c.commit.author.date.strftime("%Y-%m-%d")
                author = c.commit.author.name
                msg = c.commit.message.splitlines()[0][:80]
                lines.append(f"{sha} {date} {author} — {msg}")
            return "\n".join(lines) if lines else "(no commits)"
        except Exception as exc:
            return f"(commits error: {exc})"

    def get_default_branch(self) -> str:
        return self._repo.default_branch

    # ── Write tools (used at approve time) ───────────────────────────────────

    def create_branch(self, branch_name: str, from_ref: str | None = None) -> None:
        """Create a new branch from from_ref (default: default branch)."""
        base = from_ref or self.get_default_branch()
        sha = self._repo.get_branch(base).commit.sha
        self._repo.create_git_ref(ref=f"refs/heads/{branch_name}", sha=sha)
        log.info("github.branch_created", branch=branch_name, base=base)

    def update_file(
        self,
        path: str,
        new_content: str,
        commit_message: str,
        branch: str,
    ) -> None:
        """Create or update a file on the given branch."""
        try:
            existing = self._repo.get_contents(path, ref=branch)
            sha = existing.sha if not isinstance(existing, list) else None  # type: ignore[union-attr]
        except Exception:
            sha = None

        if sha:
            self._repo.update_file(path, commit_message, new_content, sha, branch=branch)
            log.info("github.file_updated", path=path, branch=branch)
        else:
            self._repo.create_file(path, commit_message, new_content, branch=branch)
            log.info("github.file_created", path=path, branch=branch)

    def create_pr(self, branch: str, title: str, body: str) -> str:
        """Open a pull request from branch → default branch. Returns PR URL."""
        base = self.get_default_branch()
        pr = self._repo.create_pull(title=title, body=body, head=branch, base=base)
        log.info("github.pr_created", pr_url=pr.html_url, branch=branch)
        return pr.html_url


# ── Diff helper ───────────────────────────────────────────────────────────────

def compute_diff(path: str, old_content: str, new_content: str) -> str:
    """Return a unified diff string for display."""
    diff_lines = difflib.unified_diff(
        old_content.splitlines(keepends=True),
        new_content.splitlines(keepends=True),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
    )
    return "".join(diff_lines)
