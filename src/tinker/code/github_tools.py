"""GitHub code provider — tools for the fix agent to read and write code.

Used by POST /api/v1/fix (read: get_file, search_code, get_commits)
and POST /api/v1/approve (write: create_branch, update_files, create_pr).

Requires GITHUB_TOKEN and GITHUB_REPO in config.
"""

from __future__ import annotations

import difflib
import structlog
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from github.Repository import Repository

log = structlog.get_logger(__name__)

# Cached flat file tree per repo (sha → list[path]).  Avoids re-fetching the
# tree on every tool call within the same agent loop.
_tree_cache: dict[str, list[str]] = {}


def _repo_file_tree(repo: "Repository") -> list[str]:
    """Return all blob paths in the repo using the recursive git tree API.

    Results are cached by the HEAD commit SHA so they stay fresh across deploys
    without hammering the API.
    """
    head_sha = repo.get_branch(repo.default_branch).commit.sha
    if head_sha in _tree_cache:
        return _tree_cache[head_sha]
    tree = repo.get_git_tree(head_sha, recursive=True)
    paths = [item.path for item in tree.tree if item.type == "blob"]
    _tree_cache[head_sha] = paths
    return paths


def _resolve_path(repo: "Repository", path: str) -> str | None:
    path = path.lstrip("/")
    """Find the best repo path for a (possibly container-relative) stack path.

    Matching strategy (first match wins):
      1. Exact match
      2. Any repo path whose suffix equals *path*           (handles leading /app/, /src/, etc.)
      3. Any repo path whose basename equals basename(path) (single unique filename)

    Returns the resolved repo path, or None if no match.
    """
    import os as _os
    all_paths = _repo_file_tree(repo)

    # 1. Exact
    if path in all_paths:
        return path

    # 2. Suffix match — strip leading slashes so comparison is clean
    clean = path.lstrip("/")
    suffix_matches = [p for p in all_paths if p.endswith(clean)]
    if len(suffix_matches) == 1:
        return suffix_matches[0]
    if len(suffix_matches) > 1:
        # Return the shortest (most specific) match
        return min(suffix_matches, key=len)

    # 3. Filename match
    basename = _os.path.basename(path)
    name_matches = [p for p in all_paths if _os.path.basename(p) == basename]
    if len(name_matches) == 1:
        return name_matches[0]
    if len(name_matches) > 1:
        # Multiple files with same name — pick the one whose directory components
        # overlap most with the requested path
        requested_parts = set(clean.replace("\\", "/").split("/"))
        def _overlap(p: str) -> int:
            return len(set(p.split("/")) & requested_parts)
        return max(name_matches, key=_overlap)

    return None


def _normalise_repo(repo: str) -> str:
    """Accept owner/repo or a full GitHub URL — always return owner/repo."""
    repo = repo.strip().rstrip("/")
    if repo.startswith("https://github.com/"):
        repo = repo[len("https://github.com/"):]
    elif repo.startswith("http://github.com/"):
        repo = repo[len("http://github.com/"):]
    elif repo.startswith("git@github.com:"):
        repo = repo[len("git@github.com:"):].removesuffix(".git")
    return repo


class GitHubCodeProvider:
    """Wraps PyGitHub — all repo operations go through here.

    Repo resolution order for a given service:
      1. GITHUB_REPOS JSON map  {"payments-api": "acme/payments", ...}
      2. GITHUB_REPO            single fallback repo
      3. RuntimeError           neither is configured
    """

    def __init__(self, service: str | None = None) -> None:
        from github import Github
        from tinker import toml_config as tc

        gh_cfg = tc.get().github
        if not gh_cfg.token:
            raise RuntimeError(
                "GitHub token is not configured. "
                "Set github.token in config.toml or run `tinker init server`."
            )

        self._gh = Github(gh_cfg.token)

        # Resolve which repo to use: per-service override first, then default
        repo_name: str | None = None
        if service:
            repo_name = tc.get().get_service(service).repo

        if not repo_name:
            repo_name = gh_cfg.default_repo

        if not repo_name:
            raise RuntimeError(
                "No GitHub repository configured. "
                "Set github.default_repo = \"owner/repo\" in config.toml [github]."
            )

        # Accept full GitHub URLs — strip to owner/repo
        repo_name = _normalise_repo(repo_name)

        self._repo = self._gh.get_repo(repo_name)
        log.info("github.provider_ready", repo=repo_name, service=service)

    # ── Read tools (used in fix agent loop) ──────────────────────────────────

    def get_file(self, path: str, ref: str | None = None) -> str:
        """Return file contents, resolving container-relative stack paths automatically."""
        path = path.lstrip("/")
        kwargs: dict = {}
        if ref:
            kwargs["ref"] = ref
        try:
            content = self._repo.get_contents(path, **kwargs)
            if isinstance(content, list):
                return "(directory listing)\n" + "\n".join(c.path for c in content)
            return content.decoded_content.decode("utf-8", errors="replace")
        except Exception:
            pass

        # Exact path not found — resolve via git tree
        resolved = _resolve_path(self._repo, path)
        if resolved is None:
            return f"(not found: '{path}' — no matching file in repo tree)"

        log.debug("github.get_file_resolved", requested=path, resolved=resolved)
        try:
            content = self._repo.get_contents(resolved, **kwargs)
            if isinstance(content, list):
                return "(directory listing)\n" + "\n".join(c.path for c in content)
            return (
                f"# resolved '{path}' → '{resolved}'\n"
                + content.decoded_content.decode("utf-8", errors="replace")
            )
        except Exception as exc:
            return f"(error reading resolved path '{resolved}': {exc})"

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
        """Return recent commits touching a path, resolving container-relative paths."""
        path = path.lstrip("/")
        resolved_path: str | None = path if path != "." else None

        if resolved_path:
            try:
                self._repo.get_contents(resolved_path)
            except Exception:
                resolved = _resolve_path(self._repo, resolved_path)
                if resolved:
                    log.debug("github.get_commits_resolved", requested=path, resolved=resolved)
                    resolved_path = resolved

        try:
            commits = self._repo.get_commits(path=resolved_path)
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
        path = path.lstrip("/")
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
