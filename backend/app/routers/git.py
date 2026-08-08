"""P4 — agentic Git admin API: repo allowlist CRUD + audit log.

Repos are added to a global allowlist by owner/admin (like provider API
keys): the AI may only clone/commit/push repos listed here. Write access is
a per-repo opt-in (default read-only). Credentials (deploy keys / bot
tokens) are stored server-side and never returned by this API.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, desc

from .. import config as app_config
from .. import git_tools
from ..database import async_session, GitRepo, GitActionLog
from ..routers.auth import require_role

router = APIRouter(prefix="/api/git", tags=["git"])

VALID_ACCESS = {"read", "write"}
VALID_AUTH = {"none", "ssh_key", "token"}


class GitRepoCreate(BaseModel):
    name: str
    clone_url: str
    access: str = "read"
    auth_type: str = "none"
    credential: str = ""
    default_branch: str = "main"
    pr_preferred: bool = True
    enabled: bool = True


class GitRepoUpdate(BaseModel):
    clone_url: Optional[str] = None
    access: Optional[str] = None
    auth_type: Optional[str] = None
    credential: Optional[str] = None  # non-empty replaces; empty keeps current
    clear_credential: bool = False     # true removes the stored credential
    default_branch: Optional[str] = None
    pr_preferred: Optional[bool] = None
    enabled: Optional[bool] = None


def _validate(name: str, clone_url: str, access: str, auth_type: str, credential: str, default_branch: str):
    git_tools.sanitize_repo_name(name)
    try:
        clone = git_tools.parse_clone_url(clone_url)
    except git_tools.GitToolError as e:
        raise HTTPException(400, str(e))
    if access not in VALID_ACCESS:
        raise HTTPException(400, f"access must be one of: {', '.join(sorted(VALID_ACCESS))}")
    if auth_type not in VALID_AUTH:
        raise HTTPException(400, f"auth_type must be one of: {', '.join(sorted(VALID_AUTH))}")
    if auth_type == "token":
        if not clone_url.startswith("https://"):
            raise HTTPException(400, "token auth requires an https:// clone URL (plaintext http is not allowed)")
        if clone.user:
            raise HTTPException(400, "clone_url must not contain embedded credentials — use the credential field instead")
    if auth_type == "ssh_key" and clone.kind != "ssh":
        raise HTTPException(400, "ssh_key auth requires an ssh clone URL (git@host:owner/repo.git)")
    if auth_type != "none" and not (credential or "").strip():
        raise HTTPException(400, "credential is required when auth_type is not 'none'")
    try:
        git_tools._check_branch_name(default_branch or "main", "default")
    except git_tools.GitToolError as e:
        raise HTTPException(400, str(e))


def _repo_dict(r: GitRepo) -> dict:
    return {
        "id": r.id,
        "name": r.name,
        "clone_url": r.clone_url,
        "host": git_tools.parse_clone_url(r.clone_url).host,
        "access": r.access,
        "auth_type": r.auth_type,
        "credential_set": bool((r.credential or "").strip()),
        "default_branch": r.default_branch or "main",
        "pr_preferred": bool(r.pr_preferred),
        "enabled": bool(r.enabled),
        "created_at": r.created_at.isoformat() if r.created_at else "",
    }


@router.get("/info")
async def git_info(current_user: dict = Depends(require_role("owner", "admin"))):
    name, email = git_tools.bot_identity()
    return {
        "bot_name": name,
        "bot_email": email,
        "git_bot_name_env": app_config.settings.git_bot_name or "LLMDash",
        "git_bot_email_env": app_config.settings.git_bot_email or "",
        "note": "Set GIT_BOT_NAME / GIT_BOT_EMAIL (Admin Panel → API Keys) to customize the commit author.",
    }


@router.get("/repos")
async def list_repos(current_user: dict = Depends(require_role("owner", "admin"))):
    async with async_session() as sess:
        result = await sess.execute(select(GitRepo).order_by(GitRepo.name))
        repos = result.scalars().all()
    return [_repo_dict(r) for r in repos]


@router.post("/repos", status_code=201)
async def create_repo(req: GitRepoCreate, current_user: dict = Depends(require_role("owner", "admin"))):
    name = (req.name or "").strip()
    clone_url = (req.clone_url or "").strip()
    _validate(name, clone_url, req.access, req.auth_type, req.credential, req.default_branch)
    async with async_session() as sess:
        existing = await sess.execute(select(GitRepo.id).where(GitRepo.name == name))
        if existing.scalar_one_or_none():
            raise HTTPException(409, f"Repository '{name}' already exists in the allowlist")
        repo = GitRepo(
            name=name,
            clone_url=clone_url,
            access=req.access,
            auth_type=req.auth_type,
            credential=(req.credential or "").strip() or None,
            default_branch=(req.default_branch or "main").strip(),
            pr_preferred=req.pr_preferred,
            enabled=req.enabled,
        )
        sess.add(repo)
        await sess.commit()
        await sess.refresh(repo)
        git_tools.invalidate_credential_cache()
        await git_tools.log_action(current_user["user_id"], repo.id, "repo_add", name, True)
        return _repo_dict(repo)


@router.put("/repos/{repo_id}")
async def update_repo(repo_id: int, req: GitRepoUpdate, current_user: dict = Depends(require_role("owner", "admin"))):
    async with async_session() as sess:
        result = await sess.execute(select(GitRepo).where(GitRepo.id == repo_id))
        repo = result.scalar_one_or_none()
        if not repo:
            raise HTTPException(404, "Repository not found")

        new_url = (req.clone_url or "").strip() if req.clone_url is not None else repo.clone_url
        new_access = req.access if req.access is not None else repo.access
        new_auth = req.auth_type if req.auth_type is not None else repo.auth_type
        new_cred = repo.credential
        if req.credential is not None and (req.credential or "").strip():
            new_cred = (req.credential or "").strip()
        if req.clear_credential:
            new_cred = None
        new_branch = (req.default_branch or "").strip() if req.default_branch is not None else (repo.default_branch or "main")
        _validate(repo.name, new_url, new_access, new_auth, new_cred or "", new_branch)

        # A replaced/cleared credential stays masked in chat output forever.
        old_cred = repo.credential
        if old_cred and old_cred != new_cred:
            await git_tools.remember_credential(old_cred)

        repo.clone_url = new_url
        repo.access = new_access
        repo.auth_type = new_auth
        if req.credential is not None and (req.credential or "").strip():
            repo.credential = new_cred
        elif req.clear_credential:
            repo.credential = None
        if req.default_branch is not None:
            repo.default_branch = new_branch
        if req.pr_preferred is not None:
            repo.pr_preferred = req.pr_preferred
        if req.enabled is not None:
            repo.enabled = req.enabled
        await sess.commit()
        await sess.refresh(repo)
        git_tools.invalidate_credential_cache()
        if req.clear_credential:
            await git_tools.purge_live_credentials(repo.id)
        else:
            await git_tools.reprovision_live_credentials(repo)
        await git_tools.log_action(current_user["user_id"], repo.id, "repo_update", repo.name, True)
        return _repo_dict(repo)


@router.delete("/repos/{repo_id}")
async def delete_repo(repo_id: int, current_user: dict = Depends(require_role("owner", "admin"))):
    async with async_session() as sess:
        result = await sess.execute(select(GitRepo).where(GitRepo.id == repo_id))
        repo = result.scalar_one_or_none()
        if not repo:
            raise HTTPException(404, "Repository not found")
        name = repo.name
        if repo.credential:
            await git_tools.remember_credential(repo.credential)
        await sess.delete(repo)
        await sess.commit()
        git_tools.invalidate_credential_cache()
        await git_tools.purge_live_credentials(repo_id)
        await git_tools.log_action(current_user["user_id"], repo_id, "repo_delete", name, True)
    return {"status": "deleted"}


@router.get("/audit")
async def git_audit(limit: int = 50, current_user: dict = Depends(require_role("owner", "admin"))):
    limit = min(max(limit, 1), 200)
    async with async_session() as sess:
        result = await sess.execute(
            select(GitActionLog).order_by(desc(GitActionLog.id)).limit(limit)
        )
        rows = result.scalars().all()
    return [
        {
            "id": r.id,
            "user_id": r.user_id,
            "repo_id": r.repo_id,
            "action": r.action,
            "detail": r.detail,
            "success": bool(r.success),
            "created_at": r.created_at.isoformat() if r.created_at else "",
        }
        for r in rows
    ]
