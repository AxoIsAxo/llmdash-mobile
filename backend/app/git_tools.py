"""P4 — agentic Git engine.

All git operations run inside the per-conversation sandbox container, so the
worktree (/work/<repo>) persists for the whole chat and the model can edit
files / run tests with run_command right next to it. Credentials (per-repo
deploy keys or HTTP tokens) are stored server-side in the ``git_repos`` table
and provisioned into the container at clone time. Remote URLs stay clean (no
embedded tokens); auth flows through http.extraHeader for the clone and a
per-repo credential-helper store file afterwards. Credentials never appear in
git tool output, and run_command output is scrubbed against all configured
credentials too, so secrets cannot leak into the chat.

Guardrails implemented here:
- commits are always authored by the fixed LLMDash bot identity;
- ``repo_push`` / ``repo_pr`` refuse to push any commit not authored by the bot;
- pushes go only to the allowlisted remote (URL host+path is verified before
  every push, even if the model rewrote the worktree's origin);
- force-push is never possible through these tools;
- write access is per-repo opt-in (``access == "write"``);
- pull requests are preferred over direct pushes to the default branch.
"""

from __future__ import annotations

import asyncio
import re
import shlex
import time
import urllib.parse
from dataclasses import dataclass

from . import config as app_config
from .sandbox import (
    _docker,
    _ensure_sandbox_image,
    _get_or_create_container,
    MAX_OUTPUT_CHARS,
)

WORKTREE_ROOT = "/work"
GIT_CLONE_TIMEOUT = 120.0
GIT_CMD_TIMEOUT = 60.0

# Per-conversation locks so concurrent tool calls in one round can't race on
# the same worktree (git add/commit/push are not parallel-safe).
_locks: dict[int, asyncio.Lock] = {}

# Credential scrub-list cache (see all_credentials / remember_credential).
CRED_CACHE_TTL = 10.0
_cred_cache: list[str] | None = None
_cred_cache_ts: float = 0.0
_cache_t0 = time.monotonic

_REPO_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_CLONE_URL_RE = re.compile(r"^[^@/]+@([^/:]+):(.+)$")
# Git refs must not start with '-' or '.', end with '.', or contain ".."
# (plus the usual ~ ^ : ? * [ \ space / control bans). This blocks option
# injection ("--force"), refspec tricks ("main:evil") and path tricks ("a..b").
_BRANCH_RE = re.compile(r"^(?!.*\.\.)(?!.*\.$)[A-Za-z0-9_][A-Za-z0-9_.\-/]{0,63}$")


class GitToolError(Exception):
    """Fatal, user-visible git tool failure (message is safe to show)."""


# --------------------------------------------------------------------------
# Pure helpers (unit-testable, no Docker)
# --------------------------------------------------------------------------

def sanitize_repo_name(name: str) -> str:
    name = (name or "").strip()
    if not _REPO_NAME_RE.match(name):
        raise GitToolError(
            f"Invalid repo name {name!r}: use letters, digits, '.', '_' or '-' (max 64 chars)."
        )
    return name


@dataclass
class CloneUrl:
    kind: str              # "https" | "ssh"
    host: str
    path: str              # "owner/repo.git"
    user: str | None = None
    port: int | None = None


def parse_clone_url(url: str) -> CloneUrl:
    """Parse an https:// URL, ssh:// URL, or scp-like (git@host:path) URL."""
    url = (url or "").strip()
    if url.startswith(("https://", "http://")):
        parsed = urllib.parse.urlsplit(url)
        if not parsed.hostname:
            raise GitToolError(f"clone_url has no host: {url!r}")
        return CloneUrl("https", parsed.hostname, parsed.path.strip("/"), parsed.username, parsed.port)
    if url.startswith("ssh://"):
        parsed = urllib.parse.urlsplit(url)
        if not parsed.hostname:
            raise GitToolError(f"clone_url has no host: {url!r}")
        return CloneUrl("ssh", parsed.hostname, parsed.path.strip("/"), parsed.username, parsed.port)
    m = _CLONE_URL_RE.match(url)
    if m:
        return CloneUrl("ssh", m.group(1), m.group(2).strip("/"))
    raise GitToolError("clone_url must be an https:// URL or an ssh URL (git@host:owner/repo.git)")


def clean_https_url(clone: CloneUrl) -> str:
    """Rebuild the clone URL WITHOUT any userinfo, so a token pasted into
    clone_url by an admin can never end up in .git/config or git remote -v."""
    host = f"[{clone.host}]" if ":" in clone.host else clone.host
    if clone.port:
        host = f"{host}:{clone.port}"
    return f"https://{host}/{clone.path}"


def web_url(clone: CloneUrl) -> str:
    """https://host/<path> web root, for building compare/PR URLs (no .git suffix)."""
    host = f"[{clone.host}]" if ":" in clone.host else clone.host
    path = clone.path.rstrip("/")
    if path.lower().endswith(".git"):
        path = path[:-4]
    return f"https://{host}/{path}"


def same_remote(allowed_url: str, actual_url: str) -> bool:
    """True when both URLs point at the same host + path (scheme/user ignored).

    Used to verify `git remote get-url origin` still matches the allowlisted
    repo before pushing — a model that rewrote the remote to another path
    must not push with the stored credential.
    """
    try:
        a = parse_clone_url(allowed_url)
        b = parse_clone_url(actual_url)
    except GitToolError:
        return False
    return a.host.lower() == b.host.lower() and a.path.rstrip("/").lower() == b.path.rstrip("/").lower()


def bot_identity(clone: CloneUrl | None = None) -> tuple[str, str]:
    """The fixed commit author. Never the user's git identity."""
    name = (app_config.settings.git_bot_name or "LLMDash").strip() or "LLMDash"
    email = (app_config.settings.git_bot_email or "").strip()
    if not email:
        host = clone.host if clone else "localhost"
        email = f"llmdash@{host}"
    return name, email


def _token_parts(credential: str | None) -> tuple[str, str]:
    """'user:token' -> (user, token); bare 'token' -> ('oauth2', token)."""
    cred = (credential or "").strip()
    if not cred:
        raise GitToolError("No credential configured for this repository.")
    if ":" in cred:
        user, _, tok = cred.partition(":")
        return (user or "oauth2"), tok
    return "oauth2", cred


def _basic_auth(user: str, tok: str) -> str:
    """HTTP Basic auth header value for git's http.extraHeader."""
    import base64

    return "Basic " + base64.b64encode(f"{user}:{tok}".encode("utf-8")).decode("ascii")


def _token_credentials(repo) -> tuple[str, str, str]:
    """(user, token, basic_auth_header) for a token-auth repo."""
    user, tok = _token_parts(repo.credential)
    return user, tok, _basic_auth(user, tok)


def _store_line(repo) -> str:
    """Credential-store line for the repo, scoped to host AND path so the
    token is never offered to other repositories on the same host."""
    user, tok = _token_parts(repo.credential)
    clone = parse_clone_url(repo.clone_url)
    host = clone.host if not clone.port else f"{clone.host}:{clone.port}"
    return f"https://{urllib.parse.quote(user)}:{urllib.parse.quote(tok)}@{host}/{clone.path}"


async def all_credentials() -> list[str]:
    """Every stored git credential — current + historical — for scrubbing
    run_command output. Cached briefly; invalidated on repo mutations so a
    freshly rotated token is masked immediately."""
    global _cred_cache, _cred_cache_ts
    now = _cache_t0()
    if _cred_cache is not None and now - _cred_cache_ts < CRED_CACHE_TTL:
        return _cred_cache
    try:
        from sqlalchemy import select
        from .database import async_session, GitRepo, GitCredentialHistory
    except Exception:
        return []
    secrets: list[str] = []
    try:
        async with async_session() as sess:
            result = await sess.execute(select(GitRepo))
            for r in result.scalars().all():
                secrets.extend(_secrets_for(r))
            hist = await sess.execute(select(GitCredentialHistory))
            for h in hist.scalars().all():
                if (h.secret or "").strip():
                    secrets.append(h.secret)
    except Exception:
        pass
    _cred_cache = secrets
    _cred_cache_ts = now
    return secrets


def invalidate_credential_cache() -> None:
    global _cred_cache, _cred_cache_ts
    _cred_cache = None
    _cred_cache_ts = 0.0


async def remember_credential(secret: str) -> None:
    """Persist a replaced/cleared credential so it stays scrubbed even after
    it leaves the repo row (rotation, clear, delete)."""
    secret = (secret or "").strip()
    if not secret or len(secret) < 6:
        return
    try:
        from .database import async_session, GitCredentialHistory
    except Exception:
        return
    try:
        async with async_session() as sess:
            sess.add(GitCredentialHistory(secret=secret))
            await sess.commit()
    except Exception:
        pass
    invalidate_credential_cache()


def _secrets_for(repo) -> list[str]:
    """Credential strings to scrub from ANY tool output (git tools and
    run_command) so they can never reach the chat. Includes the raw token,
    the user:token pair, and its base64 Basic-auth form (visible in the
    clone command line via `ps` inside the container)."""
    auth_type = (repo.auth_type or "none").lower()
    if auth_type == "token":
        try:
            user, tok = _token_parts(repo.credential)
        except GitToolError:
            return []
        return [tok, f"{user}:{tok}", _basic_auth(user, tok)]
    if auth_type == "ssh_key":
        key = (repo.credential or "").strip()
        out = []
        for line in key.splitlines():
            if len(line) >= 6:
                out.append(line)
        if key and len(key) >= 6:
            out.append(key)  # whole PEM, for `cat`-style dumps
        return out
    return []


def _redact(text: str, secrets: list[str]) -> str:
    for s in secrets:
        if s and len(s) >= 6:
            text = text.replace(s, "***")
    return text


def _cap(text: str) -> str:
    if len(text) > MAX_OUTPUT_CHARS:
        return text[:MAX_OUTPUT_CHARS] + f"\n\n[Output truncated at {MAX_OUTPUT_CHARS} chars]"
    return text


# --------------------------------------------------------------------------
# Container plumbing (reuses the per-conversation sandbox)
# --------------------------------------------------------------------------

def _lock_for(conversation_id: int) -> asyncio.Lock:
    return _locks.setdefault(conversation_id, asyncio.Lock())


async def _exec(container: str, cmd: str, timeout: float = GIT_CMD_TIMEOUT) -> tuple[int, str, str]:
    rc, out, err = await _docker("exec", container, "sh", "-c", cmd, timeout=timeout)
    return rc, out, err


async def _ensure_container(conversation_id: int) -> str:
    if not conversation_id:
        raise GitToolError("Error: git tools require an active conversation.")
    err = await _ensure_sandbox_image()
    if err:
        raise GitToolError(err)
    container = await _get_or_create_container(conversation_id)
    if not container:
        raise GitToolError("Error: could not start the sandbox container for this conversation.")
    # Register with the sandbox sweeper so idle git-created containers are
    # reaped like run_command ones (same idle TTL / pool cap).
    import time

    from .sandbox import _sandboxes
    _sandboxes[conversation_id] = {"container": container, "last_used": time.monotonic()}
    return container


def worktree_path(repo_name: str) -> str:
    return f"{WORKTREE_ROOT}/{sanitize_repo_name(repo_name)}"


def _ssh_command(repo_id: int) -> str:
    return (
        "ssh -i /root/.ssh/llmdash_{rid} -o IdentitiesOnly=yes "
        "-o StrictHostKeyChecking=accept-new "
        "-o UserKnownHostsFile=/root/.ssh/known_hosts_{rid}"
    ).format(rid=repo_id)


async def _ensure_cloned(container: str, workdir: str) -> bool:
    rc, _, _ = await _exec(container, f"test -d {shlex.quote(workdir)}/.git", 15)
    return rc == 0


# --------------------------------------------------------------------------
# Public operations
# --------------------------------------------------------------------------

def _clean_auth_error(text: str, auth_type: str) -> str:
    """Make token-auth failures readable: git falls back to a credential
    prompt when a token is rejected, which surfaces as 'could not read
    Username' instead of the real cause."""
    if auth_type == "token" and ("could not read Username" in text or "could not read Password" in text):
        return text + "\n(Authentication failed — check the repository's bot token.)"
    return text


async def clone_repo(repo, conversation_id: int) -> str:
    """Clone an allowlisted repo into the conversation worktree (idempotent)."""
    name = sanitize_repo_name(repo.name)
    clone = parse_clone_url(repo.clone_url)
    async with _lock_for(conversation_id):
        container = await _ensure_container(conversation_id)
        workdir = worktree_path(name)
        if await _ensure_cloned(container, workdir):
            return (
                f"Repository '{name}' is already cloned in this conversation's worktree "
                f"({workdir}). Nothing to do."
            )

        await _exec(container, f"mkdir -p {shlex.quote(workdir)}", 15)

        auth_type = (repo.auth_type or "none").lower()
        # Always clone the CLEAN url — any userinfo an admin pasted into
        # clone_url is stripped so it can never land in .git/config.
        url = clean_https_url(clone) if clone.kind == "https" else repo.clone_url
        secrets: list[str] = []
        extra_header = ""
        if auth_type == "token":
            if clone.kind != "https" or not repo.clone_url.startswith("https://"):
                raise GitToolError("token auth requires an https:// clone URL")
            user, tok, basic = _token_credentials(repo)
            secrets = [tok, f"{user}:{tok}", basic]
            # Auth travels via http.extraHeader for the clone and a per-repo
            # credential-helper store file for later fetch/push. The remote URL
            # stays CLEAN (no embedded token), so `git remote -v` / .git/config
            # never leak the credential into the chat or to the model. The store
            # line includes the repo path and useHttpPath is set, so the token is
            # only ever offered for THIS repository — never other repos on the
            # same host.
            cred_file = f"/root/.git-credentials-{repo.id}"
            script = f"printf '%s' {shlex.quote(_store_line(repo))} > {cred_file} && chmod 600 {cred_file}"
            r, _, e = await _exec(container, script, 15)
            if r != 0:
                raise GitToolError(f"Error: could not provision the credential: {e.strip()[:300]}")
            extra_header = f"-c http.extraHeader={shlex.quote('Authorization: ' + basic)} "
        elif auth_type == "ssh_key":
            key = (repo.credential or "").strip()
            if not key:
                raise GitToolError("No deploy key configured for this repository.")
            script = (
                "mkdir -p /root/.ssh && chmod 700 /root/.ssh && "
                f"printf '%s' {shlex.quote(key)} > /root/.ssh/llmdash_{repo.id} && "
                f"chmod 600 /root/.ssh/llmdash_{repo.id}"
            )
            r, _, e = await _exec(container, script, 15)
            if r != 0:
                raise GitToolError(f"Error: could not provision the deploy key: {e.strip()[:300]}")

        cmd = f"git clone --quiet {shlex.quote(url)} {shlex.quote(workdir)}"
        if auth_type == "ssh_key":
            cmd = f"git -c core.sshCommand={shlex.quote(_ssh_command(repo.id))} clone --quiet {shlex.quote(url)} {shlex.quote(workdir)}"
        elif auth_type == "token":
            cmd = f"git {extra_header}clone --quiet {shlex.quote(url)} {shlex.quote(workdir)}"
        rc, _, err = await _exec(container, cmd, GIT_CLONE_TIMEOUT)
        if rc != 0:
            raise GitToolError(
                f"Error: clone failed for '{name}': {_clean_auth_error(_redact(err.strip(), secrets), auth_type)[:500]}"
            )

        bot_name, bot_email = bot_identity(clone)
        await _exec(
            container,
            f"git -C {shlex.quote(workdir)} config user.name {shlex.quote(bot_name)} && "
            f"git -C {shlex.quote(workdir)} config user.email {shlex.quote(bot_email)}",
            15,
        )
        if auth_type == "ssh_key":
            await _exec(
                container,
                f"git -C {shlex.quote(workdir)} config core.sshCommand {shlex.quote(_ssh_command(repo.id))}",
                15,
            )
        elif auth_type == "token":
            await _exec(
                container,
                f"git -C {shlex.quote(workdir)} config credential.helper {shlex.quote(f'store --file={cred_file}')} && "
                f"git -C {shlex.quote(workdir)} config credential.useHttpPath true",
                15,
            )

    return (
        f"Cloned '{name}' into the conversation worktree ({workdir}).\n"
        f"Remote: {clone.host}/{clone.path}\n"
        f"Commit author is fixed to {bot_name} <{bot_email}> — I can now edit files "
        f"(via run_command or by asking me), then use git_status / git_diff / git_commit "
        f"/ git_push / git_pr."
    )


async def run_git(repo, conversation_id: int, args: list[str], timeout: float = GIT_CMD_TIMEOUT) -> str:
    """Run a git command in the repo's worktree; raises GitToolError on
    failure. Callers MUST hold the per-conversation lock (multi-step
    operations acquire it once at entry so commands can't interleave)."""
    name = sanitize_repo_name(repo.name)
    container = await _ensure_container(conversation_id)
    workdir = worktree_path(name)
    if not await _ensure_cloned(container, workdir):
        raise GitToolError(f"Repository '{name}' is not cloned in this conversation yet — call git_clone first.")
    cmd = "git -C " + shlex.quote(workdir) + " " + " ".join(shlex.quote(a) for a in args)
    rc, out, err = await _exec(container, cmd, timeout)
    text = out
    if err.strip():
        text += "\n--- stderr ---\n" + err
    # Redact BEFORE capping so a token split at the truncation boundary can't leak.
    text = _cap(_redact(text, _secrets_for(repo)))
    if rc != 0:
        raise GitToolError(text.strip()[:800] or f"git {' '.join(args)} failed")
    return text


def _check_branch_name(branch: str, what: str) -> str:
    branch = (branch or "").strip()
    if not _BRANCH_RE.match(branch):
        raise GitToolError(
            f"Invalid {what} branch name {branch!r}: use letters, digits, '.', '_', '-' or '/' (max 64 chars)."
        )
    return branch


def require_write(repo) -> None:
    if (repo.access or "read").lower() != "write":
        raise GitToolError(
            f"Repository '{repo.name}' is read-only. Ask an admin to grant write access "
            f"(Admin Panel → Git) before committing or pushing."
        )


async def repo_status(repo, conversation_id: int) -> str:
    async with _lock_for(conversation_id):
        return await _repo_status_locked(repo, conversation_id)


async def _repo_status_locked(repo, conversation_id: int) -> str:
    out = []
    try:
        branch = (await run_git(repo, conversation_id, ["rev-parse", "--abbrev-ref", "HEAD"], 15)).strip()
    except GitToolError as e:
        return f"Error: {e}"
    out.append(f"Branch: {branch}")
    out.append(await run_git(repo, conversation_id, ["status", "--short", "--branch"], 15))
    try:
        upstream = (await run_git(repo, conversation_id, ["rev-parse", "--abbrev-ref", "@{u}"], 15)).strip()
    except GitToolError:
        upstream = ""
    ahead = ""
    if upstream:
        ahead = (await run_git(repo, conversation_id, ["rev-list", "--count", "@{u}..HEAD"], 15)).strip()
        out.append(f"Upstream: {upstream} (ahead by {ahead} commits)")
    log = await run_git(repo, conversation_id, ["log", "--oneline", "-10"], 15)
    out.append("Recent commits:\n" + log)
    return "\n".join(out)


async def repo_diff(repo, conversation_id: int, scope: str, path: str | None = None) -> str:
    async with _lock_for(conversation_id):
        return await _repo_diff_locked(repo, conversation_id, scope, path)


async def _repo_diff_locked(repo, conversation_id: int, scope: str, path: str | None = None) -> str:
    scope = (scope or "unstaged").strip().lower()
    valid = {"unstaged", "staged", "all", "vs_remote"}
    if scope not in valid:
        raise GitToolError(f"diff scope must be one of: {', '.join(sorted(valid))}")
    args: list[str] = []
    label = "Unstaged changes"
    if scope == "unstaged":
        args = ["diff"]
        label = "Unstaged changes"
    elif scope == "staged":
        args = ["diff", "--cached"]
        label = "Staged changes"
    elif scope == "all":
        args = ["diff", "HEAD"]
        label = "Changes vs HEAD"
    elif scope == "vs_remote":
        branch = (await run_git(repo, conversation_id, ["rev-parse", "--abbrev-ref", "HEAD"], 15)).strip()
        try:
            await run_git(repo, conversation_id, ["fetch", "origin", branch], 45)
        except GitToolError:
            pass
        args = ["diff", f"origin/{branch}...HEAD"]
        label = "Changes vs origin"
    if path:
        args.append("--")
        args.append(path)
    try:
        body = await run_git(repo, conversation_id, args, 45)
    except GitToolError as e:
        return f"Error: {e}"
    if not body.strip() or body.strip() in ("\n", ""):
        return f"{label}: (no differences)"
    return f"{label}:\n```diff\n{body.rstrip()}\n```"


async def repo_commit(repo, conversation_id: int, message: str, files: list[str] | None = None) -> str:
    async with _lock_for(conversation_id):
        return await _repo_commit_locked(repo, conversation_id, message, files)


async def _repo_commit_locked(repo, conversation_id: int, message: str, files: list[str] | None = None) -> str:
    require_write(repo)
    message = (message or "").strip()
    if not message:
        raise GitToolError("A commit message is required.")
    if isinstance(files, str):  # models may pass a single path instead of a list
        files = [files]
    paths = [str(p).strip() for p in (files or []) if str(p).strip()]
    if paths:
        await run_git(repo, conversation_id, ["add", "--"] + paths, 30)
    else:
        await run_git(repo, conversation_id, ["add", "-A"], 30)
    clone = parse_clone_url(repo.clone_url)
    bot_name, bot_email = bot_identity(clone)
    try:
        out = await run_git(
            repo, conversation_id,
            ["-c", f"user.name={bot_name}", "-c", f"user.email={bot_email}",
             "commit", "-m", message],
            30,
        )
    except GitToolError as e:
        raise GitToolError(f"Commit failed: {e}")
    author = (await run_git(repo, conversation_id, ["log", "-1", "--format=%an <%ae>"], 15)).strip()
    return f"{out.strip()}\nAuthor: {author}"


def _parse_log_offenders(log_text: str, bot_name: str, bot_email: str) -> list[str]:
    """Parse `git log --format=%H|%an|%ae` output; return lines whose author
    is not the bot identity. Pure function (unit-testable)."""
    expected = f"{bot_name} <{bot_email}>"
    offenders = []
    for line in (log_text or "").splitlines():
        if not line.strip():
            continue
        try:
            h, an, ae = line.split("|", 2)
        except ValueError:
            continue
        if f"{an} <{ae}>".strip() != expected:
            offenders.append(f"{h[:12]} — authored by {an} <{ae}>")
    return offenders


async def _unbot_authored_commits(repo, conversation_id: int) -> list[str]:
    """Commits reachable from HEAD but not yet on any origin branch whose
    author is not the bot identity. Empty list = safe to push."""
    clone = parse_clone_url(repo.clone_url)
    bot_name, bot_email = bot_identity(clone)
    refs_out = await run_git(repo, conversation_id, ["for-each-ref", "--format=%(refname)", "refs/remotes/origin"], 15)
    remote_refs = [r for r in refs_out.split() if r]
    if remote_refs:
        log = await run_git(repo, conversation_id, ["log", "--format=%H|%an|%ae", "--not"] + remote_refs + ["HEAD"], 30)
    else:
        log = await run_git(repo, conversation_id, ["log", "--format=%H|%an|%ae", "HEAD"], 30)
    return _parse_log_offenders(log, bot_name, bot_email)


async def _verify_origin(repo, conversation_id: int) -> None:
    """Ensure the worktree's origin still matches the allowlisted URL. Called
    before any push (repo_push and repo_pr) so a model that rewrote the remote
    can't push with the stored credential to a different target."""
    try:
        origin_url = (await run_git(repo, conversation_id, ["remote", "get-url", "origin"], 15)).strip()
    except GitToolError as e:
        raise GitToolError(f"Error: {e}")
    if not same_remote(repo.clone_url, origin_url):
        raise GitToolError(
            f"Refusing to push: the worktree's origin remote ({origin_url}) no longer matches "
            f"the allowlisted URL for '{repo.name}'. Reset the remote to the allowlisted URL first."
        )


async def repo_push(repo, conversation_id: int) -> str:
    async with _lock_for(conversation_id):
        return await _repo_push_locked(repo, conversation_id)


async def _repo_push_locked(repo, conversation_id: int) -> str:
    require_write(repo)
    branch = _check_branch_name(
        (await run_git(repo, conversation_id, ["rev-parse", "--abbrev-ref", "HEAD"], 15)).strip(),
        "source",
    )
    default_branch = (repo.default_branch or "main").strip()
    if (repo.pr_preferred if repo.pr_preferred is not None else True) and branch == default_branch:
        raise GitToolError(
            f"You are on the default branch '{branch}' and this repo prefers pull requests. "
            f"Create a feature branch (e.g. via run_command: git checkout -b fix/...), commit "
            f"your changes there, and then call git_pr to open a merge request."
        )

    # Only push to the allowlisted remote: verify origin still matches.
    await _verify_origin(repo, conversation_id)

    # Author-enforcement: every not-yet-pushed commit must be bot-authored.
    offenders = await _unbot_authored_commits(repo, conversation_id)
    if offenders:
        raise GitToolError(
            "Refusing to push: these commits are not authored by the LLMDash bot identity:\n"
            + "\n".join(offenders)
            + "\nAmend or re-commit them as the bot before pushing."
        )

    out = await run_git(repo, conversation_id, ["push", "origin", branch], 60)
    return _clean_auth_error(out.strip(), (repo.auth_type or "none").lower()) or f"Pushed branch '{branch}' to origin."


async def repo_pr(
    repo,
    conversation_id: int,
    title: str,
    description: str = "",
    source_branch: str = "",
    target_branch: str = "",
) -> str:
    async with _lock_for(conversation_id):
        return await _repo_pr_locked(repo, conversation_id, title, description, source_branch, target_branch)


async def _repo_pr_locked(
    repo,
    conversation_id: int,
    title: str,
    description: str = "",
    source_branch: str = "",
    target_branch: str = "",
) -> str:
    title = (title or "").strip()
    if not title:
        raise GitToolError("A PR title is required.")
    clone = parse_clone_url(repo.clone_url)
    default_branch = (repo.default_branch or "main").strip()

    # Determine branches: source defaults to the current branch, target to the default branch.
    if not source_branch:
        source_branch = (await run_git(repo, conversation_id, ["rev-parse", "--abbrev-ref", "HEAD"], 15)).strip()
    source_branch = _check_branch_name(source_branch, "source")
    target_branch = _check_branch_name(target_branch or default_branch, "target")

    # Push the source branch first (author-enforced) so the PR has a head.
    require_write(repo)
    await _verify_origin(repo, conversation_id)
    offenders = await _unbot_authored_commits(repo, conversation_id)
    if offenders:
        raise GitToolError(
            "Refusing to open a PR: these commits are not authored by the LLMDash bot identity:\n"
            + "\n".join(offenders)
        )
    try:
        await run_git(repo, conversation_id, ["push", "origin", source_branch], 60)
    except GitToolError as e:
        raise GitToolError(
            f"Could not push source branch: {_clean_auth_error(str(e), (repo.auth_type or 'none').lower())}"
        )

    token = ""
    if (repo.auth_type or "none").lower() == "token":
        try:
            _, token = _token_parts(repo.credential)  # bare token, not "user:token"
        except GitToolError:
            token = ""
    if not token:
        # No API token: give the model a manual compare URL to hand to the user.
        return _manual_pr_url(clone, source_branch, target_branch)

    return await _create_pr_via_api(clone, token, title, description or "", source_branch, target_branch)


def _manual_pr_url(clone: CloneUrl, source_branch: str, target_branch: str) -> str:
    base = web_url(clone).rstrip("/")
    host = clone.host.lower()
    if host == "github.com":
        return f"{base}/compare/{target_branch}...{source_branch}?expand=1"
    if host == "gitlab.com":
        q = urllib.parse.urlencode({
            "merge_request[source_branch]": source_branch,
            "merge_request[target_branch]": target_branch,
        })
        return f"{base}/-/merge_requests/new?{q}"
    return f"{base}/compare/{target_branch}...{source_branch}"


def _api_base(clone: CloneUrl) -> tuple[str, str]:
    """(api_base_url, host_kind) for the repo's forge. host_kind is one of
    "github" | "gitlab" | "gitea". Everything that is not github.com /
    gitlab.com is assumed to be a Gitea/Forgejo instance (covers Codeberg)."""
    host = clone.host.lower()
    if host == "github.com":
        return "https://api.github.com", "github"
    if host == "gitlab.com":
        return "https://gitlab.com/api/v4", "gitlab"
    return f"https://{clone.host}/api/v1", "gitea"


async def _create_pr_via_api(clone: CloneUrl, token: str, title: str, description: str, source_branch: str, target_branch: str) -> str:
    import httpx

    api_base, kind = _api_base(clone)
    owner, _, repo_name = clone.path.rstrip("/").partition("/")
    repo_name = repo_name.removesuffix(".git")
    headers = {"Accept": "application/json"}
    if kind == "github":
        headers["Authorization"] = f"token {token}"
        url = f"{api_base}/repos/{owner}/{repo_name}/pulls"
        payload = {"title": title, "head": source_branch, "base": target_branch, "body": description}
    elif kind == "gitlab":
        headers["PRIVATE-TOKEN"] = token
        project = urllib.parse.quote(f"{owner}/{repo_name}", safe="")
        url = f"{api_base}/projects/{project}/merge_requests"
        payload = {"source_branch": source_branch, "target_branch": target_branch, "title": title, "description": description}
    else:  # gitea / forgejo / codeberg
        headers["Authorization"] = f"token {token}"
        url = f"{api_base}/repos/{owner}/{repo_name}/pulls"
        payload = {"title": title, "head": source_branch, "base": target_branch, "body": description}

    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
    except Exception as e:
        return _manual_pr_url(clone, source_branch, target_branch) + f"\n(API call failed: {e})"

    if resp.status_code in (200, 201):
        try:
            data = resp.json()
            html = data.get("html_url") or data.get("web_url") or data.get("url")
            if html:
                return f"Pull request created: {html}"
        except Exception:
            pass
        return f"Pull request created (status {resp.status_code})."
    detail = ""
    try:
        err = resp.json()
        if isinstance(err, dict):
            detail = str(err.get("message") or err.get("error") or err)[:300]
    except Exception:
        detail = resp.text[:300]
    return (
        f"Could not create the PR via API (HTTP {resp.status_code}: {detail}).\n"
        f"Manual URL: {_manual_pr_url(clone, source_branch, target_branch)}"
    )


# --------------------------------------------------------------------------
# Audit log (mirrors token_usage_log-style bookkeeping)
# --------------------------------------------------------------------------

async def reprovision_live_credentials(repo) -> None:
    """Rewrite the provisioned credential (token store file / ssh key) in every
    live sandbox container, so a rotated token or key takes effect without
    re-cloning. Silently skips containers that never cloned the repo."""
    auth_type = (repo.auth_type or "none").lower()
    if auth_type == "none" or not (repo.credential or "").strip():
        return
    try:
        from .sandbox import _sandboxes
    except Exception:
        return
    containers = {e["container"] for e in _sandboxes.values()}
    for container in containers:
        try:
            if auth_type == "token":
                script = (
                    f"printf '%s' {shlex.quote(_store_line(repo))} > /root/.git-credentials-{repo.id} && "
                    f"chmod 600 /root/.git-credentials-{repo.id} && "
                    f"git -C {shlex.quote(worktree_path(repo.name))} config credential.useHttpPath true 2>/dev/null || true"
                )
            else:  # ssh_key
                key = (repo.credential or "").strip()
                if not key:
                    continue
                script = (
                    "mkdir -p /root/.ssh && chmod 700 /root/.ssh && "
                    f"printf '%s' {shlex.quote(key)} > /root/.ssh/llmdash_{repo.id} && "
                    f"chmod 600 /root/.ssh/llmdash_{repo.id}"
                )
            await _exec(container, script, 15)
        except Exception:
            continue


async def purge_live_credentials(repo_id: int) -> None:
    """Remove a repo's provisioned credential files from every live sandbox
    container (called when a credential is cleared or the repo is deleted, so
    a revoked credential stops being usable in live worktrees)."""
    try:
        from .sandbox import _sandboxes
    except Exception:
        return
    containers = {e["container"] for e in _sandboxes.values()}
    for container in containers:
        try:
            await _exec(
                container,
                f"rm -f /root/.git-credentials-{repo_id} /root/.ssh/llmdash_{repo_id}",
                15,
            )
        except Exception:
            continue


async def log_action(user_id: int | None, repo_id: int | None, action: str, detail: str = "", success: bool = True) -> None:
    from .database import async_session, GitActionLog

    try:
        async with async_session() as sess:
            sess.add(GitActionLog(
                user_id=user_id,
                repo_id=repo_id,
                action=action[:64],
                detail=(detail or "")[:2000],
                success=success,
            ))
            await sess.commit()
    except Exception:
        pass  # audit must never break the chat flow
