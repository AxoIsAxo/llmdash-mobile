"""P12 — skill marketplace API.

Admin/owner only (install runs arbitrary skill code in the server process,
so it needs the review gate): catalog + installed skills, install from a git
URL, approve (review gate — users only see approved skills) and uninstall
(removes code + per-user rows instantly). Gated by the ``skill_marketplace``
entitlement AND the owner/admin role.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..skills import marketplace_store
from ..skills.marketplace_store import (
    approve, install_from_url, list_installed, read_catalog, uninstall,
)
from .auth import require_entitlement, require_role

router = APIRouter(prefix="/api/marketplace", tags=["marketplace"])

_ADMIN = require_role("owner", "admin")
_MARKET = require_entitlement("skill_marketplace")


class InstallRequest(BaseModel):
    url: str


@router.get("")
async def marketplace_overview(
    current_user: dict = Depends(_MARKET),
    _admin: dict = Depends(_ADMIN),
):
    return {
        "catalog": read_catalog(),
        "installed": list_installed(),
    }


@router.post("/install", status_code=201)
async def install_skill(
    req: InstallRequest,
    current_user: dict = Depends(_MARKET),
    _admin: dict = Depends(_ADMIN),
):
    try:
        return await asyncio.to_thread(install_from_url, req.url)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:  # import errors etc. -> 400, never a 500
        raise HTTPException(400, f"Install failed: {str(exc)[:300]}")


@router.post("/{name}/approve")
async def approve_skill(
    name: str,
    current_user: dict = Depends(_MARKET),
    _admin: dict = Depends(_ADMIN),
):
    try:
        approve(name)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    return {"status": "approved", "name": name}


@router.delete("/{name}")
async def uninstall_skill(
    name: str,
    current_user: dict = Depends(_MARKET),
    _admin: dict = Depends(_ADMIN),
):
    try:
        await uninstall(name)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    return {"status": "uninstalled", "name": name}
