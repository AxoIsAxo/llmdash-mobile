"""P4 — agentic Git skills: clone / status / diff / commit / push / PR.

Every commit these tools create is authored by the fixed LLMDash bot identity
(see app.git_tools.bot_identity) — never the user's git identity. Pushes are
author-enforced, restricted to the allowlisted remote, and refuse force ops.
"""

from __future__ import annotations

from sqlalchemy import select, or_

from ..base import Skill
from ...database import async_session, GitRepo
from ... import git_tools


async def _enabled_repos(user_id: int | None = None) -> list[GitRepo]:
    """Repos the user may work in: their own personal repos plus admin-shared
    global repos (user_id NULL)."""
    async with async_session() as sess:
        query = select(GitRepo).where(GitRepo.enabled == True)
        if user_id:
            query = query.where(
                or_(GitRepo.user_id == user_id, GitRepo.user_id.is_(None))
            )
        result = await sess.execute(query.order_by(GitRepo.name))
        return list(result.scalars().all())


async def _resolve_repo(repo_arg: str | None, conversation_id: int, user_id: int | None) -> GitRepo:
    repos = await _enabled_repos(user_id)
    if not repos:
        raise git_tools.GitToolError(
            "No Git repositories are configured for you. Add your own in the "
            "Agent tab → Git, or ask an admin to share one."
        )
    if repo_arg:
        wanted = str(repo_arg).strip().lower()
        for r in repos:
            if r.name.lower() == wanted:
                return r
        raise git_tools.GitToolError(
            f"Unknown repository '{repo_arg}'. Available: {', '.join(r.name for r in repos)}"
        )
    if len(repos) == 1:
        return repos[0]
    # Multiple repos and no name given: if the conversation has exactly one
    # worktree cloned, use it; otherwise ask for the repo name.
    cloned = []
    container = await git_tools._ensure_container(conversation_id)
    for r in repos:
        if await git_tools._ensure_cloned(container, git_tools.worktree_path(r.name)):
            cloned.append(r.name)
    if len(cloned) == 1:
        for r in repos:
            if r.name == cloned[0]:
                return r
    raise git_tools.GitToolError(
        f"Multiple repositories are configured. Specify which one with the 'repo' argument. "
        f"Available: {', '.join(r.name for r in repos)}"
    )


class GitCloneSkill(Skill):
    name = "git_clone"
    entitlement = "git_access"
    description = (
        "Clone an allowlisted Git repository into this conversation's persistent worktree "
        "so you can work in it (edit files, run tests, commit, push, open PRs). The clone "
        "persists for the whole conversation. Use when the user asks to work in a repo you "
        "haven't cloned yet. Uses the repo's server-side credential (deploy key / bot token) "
        "— never the user's git identity."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Name of an allowlisted repository (list available names by calling git_status without arguments)."},
        },
        "required": ["repo"],
    }

    async def execute(self, arguments: dict, _current_user: dict = None, _conversation_id: int = 0) -> str:
        user_id = (_current_user or {}).get("user_id")
        repo = None
        try:
            repo = await _resolve_repo(arguments.get("repo"), _conversation_id, user_id)
            result = await git_tools.clone_repo(repo, _conversation_id)
            await git_tools.log_action(user_id, repo.id, "clone", result[:500], True)
            return result
        except git_tools.GitToolError as e:
            await git_tools.log_action(user_id, getattr(repo, "id", None), "clone", str(e), False)
            return f"__TOOL_ERROR__: {e}"


class GitStatusSkill(Skill):
    name = "git_status"
    entitlement = "git_access"
    description = (
        "Show the state of the Git worktree(s): current branch, modified/untracked files, "
        "upstream and ahead/behind counts, recent commits. With no 'repo' argument it lists "
        "all configured repositories and which ones are cloned in this conversation."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Optional repository name. Omit to see an overview of all configured repos."},
        },
    }

    async def execute(self, arguments: dict, _current_user: dict = None, _conversation_id: int = 0) -> str:
        user_id = (_current_user or {}).get("user_id")
        try:
            repo_arg = arguments.get("repo")
            repos = await _enabled_repos(user_id)
            if not repos:
                raise git_tools.GitToolError("No Git repositories are configured. Admin Panel → Git.")
            if not repo_arg and len(repos) > 1:
                header = ["Configured repositories:"]
                container = await git_tools._ensure_container(_conversation_id)
                for r in repos:
                    cloned = "cloned" if await git_tools._ensure_cloned(container, git_tools.worktree_path(r.name)) else "not cloned"
                    access = (r.access or "read").lower()
                    header.append(f"  • {r.name} — {access} access — {cloned}")
                header.append("")
                header.append("Working tree status:")
                try:
                    repo = await _resolve_repo(None, _conversation_id, user_id)
                    body = await git_tools.repo_status(repo, _conversation_id)
                except git_tools.GitToolError as e:
                    body = f"({e})"
                await git_tools.log_action(user_id, None, "status", "overview", True)
                return "\n".join(header) + "\n" + body
            repo = await _resolve_repo(repo_arg, _conversation_id, user_id)
            result = await git_tools.repo_status(repo, _conversation_id)
            await git_tools.log_action(user_id, repo.id, "status", "", True)
            return result
        except git_tools.GitToolError as e:
            return f"__TOOL_ERROR__: {e}"


class GitDiffSkill(Skill):
    name = "git_diff"
    entitlement = "git_access"
    description = (
        "Show the diff of changes in the Git worktree: unstaged, staged, all local changes, "
        "or changes vs the remote branch. Use before committing or pushing so the user can "
        "review what would change."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Optional repository name."},
            "scope": {"type": "string", "description": "unstaged (default) | staged | all | vs_remote"},
            "path": {"type": "string", "description": "Optional path filter, e.g. 'src/main.py'."},
        },
    }

    async def execute(self, arguments: dict, _current_user: dict = None, _conversation_id: int = 0) -> str:
        user_id = (_current_user or {}).get("user_id")
        repo = None
        try:
            repo = await _resolve_repo(arguments.get("repo"), _conversation_id, user_id)
            result = await git_tools.repo_diff(repo, _conversation_id, arguments.get("scope", "unstaged"), arguments.get("path"))
            await git_tools.log_action(user_id, repo.id, "diff", f"scope={arguments.get('scope', 'unstaged')}", True)
            return result
        except git_tools.GitToolError as e:
            await git_tools.log_action(user_id, getattr(repo, "id", None), "diff", str(e), False)
            return f"__TOOL_ERROR__: {e}"


class GitCommitSkill(Skill):
    name = "git_commit"
    entitlement = "git_access"
    description = (
        "Stage and commit changes in the Git worktree. The commit is ALWAYS authored by the "
        "LLMDash bot identity, never the user. Only works in repositories with write access."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Optional repository name."},
            "message": {"type": "string", "description": "Commit message."},
            "files": {"type": "array", "items": {"type": "string"}, "description": "Optional list of file paths to stage. Omit to stage all changes."},
        },
        "required": ["message"],
    }

    async def execute(self, arguments: dict, _current_user: dict = None, _conversation_id: int = 0) -> str:
        user_id = (_current_user or {}).get("user_id")
        repo = None
        try:
            repo = await _resolve_repo(arguments.get("repo"), _conversation_id, user_id)
            result = await git_tools.repo_commit(repo, _conversation_id, arguments.get("message", ""), arguments.get("files"))
            await git_tools.log_action(user_id, repo.id, "commit", result[:500], True)
            return result
        except git_tools.GitToolError as e:
            await git_tools.log_action(user_id, getattr(repo, "id", None), "commit", str(e), False)
            return f"__TOOL_ERROR__: {e}"


class GitPushSkill(Skill):
    name = "git_push"
    entitlement = "git_access"
    description = (
        "Push the current branch to the repository's granted remote (origin). Refuses to push "
        "commits not authored by the LLMDash bot identity, refuses force-push, verifies the "
        "remote still matches the allowlisted URL, and prefers a pull request when on the "
        "default branch (use git_pr instead). Requires write access."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Optional repository name."},
        },
    }

    async def execute(self, arguments: dict, _current_user: dict = None, _conversation_id: int = 0) -> str:
        user_id = (_current_user or {}).get("user_id")
        repo = None
        try:
            repo = await _resolve_repo(arguments.get("repo"), _conversation_id, user_id)
            result = await git_tools.repo_push(repo, _conversation_id)
            await git_tools.log_action(user_id, repo.id, "push", result[:500], True)
            return result
        except git_tools.GitToolError as e:
            await git_tools.log_action(user_id, getattr(repo, "id", None), "push", str(e), False)
            return f"__TOOL_ERROR__: {e}"


class GitPrSkill(Skill):
    name = "git_pr"
    entitlement = "git_access"
    description = (
        "Open a pull/merge request for the current (or given) branch against the repository's "
        "default branch. Pushes the source branch first (author-enforced), then creates the PR "
        "via the forge API when a bot token is configured; otherwise returns a manual compare "
        "URL. Use instead of pushing directly to the default branch."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Optional repository name."},
            "title": {"type": "string", "description": "PR title."},
            "description": {"type": "string", "description": "Optional PR description / body."},
            "source_branch": {"type": "string", "description": "Optional source branch (defaults to the current branch)."},
            "target_branch": {"type": "string", "description": "Optional target branch (defaults to the repo's default branch)."},
        },
        "required": ["title"],
    }

    async def execute(self, arguments: dict, _current_user: dict = None, _conversation_id: int = 0) -> str:
        user_id = (_current_user or {}).get("user_id")
        repo = None
        try:
            repo = await _resolve_repo(arguments.get("repo"), _conversation_id, user_id)
            result = await git_tools.repo_pr(
                repo, _conversation_id,
                arguments.get("title", ""),
                arguments.get("description", ""),
                arguments.get("source_branch", ""),
                arguments.get("target_branch", ""),
            )
            await git_tools.log_action(user_id, repo.id, "pr", result[:500], True)
            return result
        except git_tools.GitToolError as e:
            await git_tools.log_action(user_id, getattr(repo, "id", None), "pr", str(e), False)
            return f"__TOOL_ERROR__: {e}"
