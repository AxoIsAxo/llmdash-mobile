"""P11 — per-user skill management API.

GET /api/skills returns every registered skill (builtins + marketplace) with
its manifest and the current user's state (enabled / custom config). PUT
endpoints manage the per-user row. The old admin CRUD for metadata-only DB
skills is gone — the manifest lives in the skill modules themselves.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..routers.auth import require_entitlement
from .registry import skill_registry
from .user_skills import get_user_skill_states, upsert_user_skill

router = APIRouter(prefix="/api/skills", tags=["skills"])


class SkillEnableRequest(BaseModel):
    enabled: bool


class SkillConfigRequest(BaseModel):
    config: dict


@router.get("")
async def list_skills(
    current_user: dict = Depends(require_entitlement("skills_management")),
    db: AsyncSession = Depends(get_db),
):
    states = await get_user_skill_states(db, current_user["user_id"])
    response = []
    for skill in skill_registry.list_skills():
        manifest = skill.to_manifest()
        state = states.get(skill.name, {"enabled": True, "config": {}})
        response.append({
            **manifest,
            "user_enabled": state["enabled"],
            "config": state["config"],
        })
    return response


@router.put("/{skill_name}/enable")
async def set_skill_enabled(
    skill_name: str,
    req: SkillEnableRequest,
    current_user: dict = Depends(require_entitlement("skills_management")),
    db: AsyncSession = Depends(get_db),
):
    if not skill_registry.get(skill_name):
        raise HTTPException(404, f"Unknown skill: {skill_name}")
    await upsert_user_skill(db, current_user["user_id"], skill_name, enabled=req.enabled)
    await db.commit()
    return {"status": "updated", "name": skill_name, "enabled": req.enabled}


@router.put("/{skill_name}/config")
async def set_skill_config(
    skill_name: str,
    req: SkillConfigRequest,
    current_user: dict = Depends(require_entitlement("skills_management")),
    db: AsyncSession = Depends(get_db),
):
    if not skill_registry.get(skill_name):
        raise HTTPException(404, f"Unknown skill: {skill_name}")
    import json as _json
    await upsert_user_skill(
        db, current_user["user_id"], skill_name,
        config_json=_json.dumps(req.config) if req.config else None,
    )
    await db.commit()
    return {"status": "updated", "name": skill_name, "config": req.config}
