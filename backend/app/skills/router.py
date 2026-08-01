from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from .models import SkillConfigDB
from .registry import skill_registry
from ..database import async_session
from ..routers.auth import get_current_user, require_role

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/skills", tags=["skills"])


class SkillConfigCreate(BaseModel):
    name: str
    description: str
    input_schema: dict


class SkillConfigUpdate(BaseModel):
    description: Optional[str] = None
    input_schema: Optional[dict] = None
    enabled: Optional[bool] = None


class SkillConfigResponse(BaseModel):
    id: int
    name: str
    description: str
    input_schema: dict
    source: str
    enabled: bool


@router.get("")
async def list_skills(current_user: dict = Depends(get_current_user)):
    builtin_defs = skill_registry.get_tool_definitions()
    async with async_session() as sess:
        result = await sess.execute(
            select(SkillConfigDB).where(
                (SkillConfigDB.user_id == current_user["user_id"]) | (SkillConfigDB.user_id.is_(None))
            )
        )
        db_skills = result.scalars().all()
    builtin_names = {s["name"] for s in builtin_defs}
    response = []
    for s in builtin_defs:
        response.append({"name": s["name"], "description": s["description"], "input_schema": s["input_schema"], "source": "builtin", "enabled": True})
    for s in db_skills:
        if s.name not in builtin_names:
            response.append({
                "id": s.id,
                "name": s.name,
                "description": s.description,
                "input_schema": json.loads(s.input_schema_json) if isinstance(s.input_schema_json, str) else s.input_schema_json,
                "source": s.source,
                "enabled": s.enabled,
            })
    return response


@router.post("", response_model=SkillConfigResponse)
async def create_skill(req: SkillConfigCreate, current_user: dict = Depends(require_role("owner", "admin"))):
    async with async_session() as sess:
        existing = await sess.execute(select(SkillConfigDB).where(SkillConfigDB.name == req.name))
        if existing.scalar_one_or_none():
            raise HTTPException(409, f"Skill '{req.name}' already exists")
        if skill_registry.get(req.name):
            raise HTTPException(409, f"Skill '{req.name}' conflicts with a built-in tool")
        # Skills are global (shared across admins). DB skills currently have no
        # execution backend — they are metadata-only until a loader is implemented.
        cfg = SkillConfigDB(
            name=req.name,
            description=req.description,
            input_schema_json=json.dumps(req.input_schema),
            source="db",
            user_id=None,
        )
        sess.add(cfg)
        await sess.commit()
        await sess.refresh(cfg)
        return SkillConfigResponse(
            id=cfg.id,
            name=cfg.name,
            description=cfg.description,
            input_schema=json.loads(cfg.input_schema_json),
            source=cfg.source,
            enabled=cfg.enabled,
        )


@router.put("/{skill_id}", response_model=SkillConfigResponse)
async def update_skill(skill_id: int, req: SkillConfigUpdate, current_user: dict = Depends(require_role("owner", "admin"))):
    async with async_session() as sess:
        result = await sess.execute(select(SkillConfigDB).where(SkillConfigDB.id == skill_id))
        cfg = result.scalar_one_or_none()
        if not cfg:
            raise HTTPException(404, "Skill not found")
        if req.description is not None:
            cfg.description = req.description
        if req.input_schema is not None:
            cfg.input_schema_json = json.dumps(req.input_schema)
        if req.enabled is not None:
            cfg.enabled = req.enabled
        await sess.commit()
        await sess.refresh(cfg)
        return SkillConfigResponse(
            id=cfg.id,
            name=cfg.name,
            description=cfg.description,
            input_schema=json.loads(cfg.input_schema_json),
            source=cfg.source,
            enabled=cfg.enabled,
        )


@router.delete("/{skill_id}")
async def delete_skill(skill_id: int, current_user: dict = Depends(require_role("owner", "admin"))):
    async with async_session() as sess:
        result = await sess.execute(select(SkillConfigDB).where(SkillConfigDB.id == skill_id))
        cfg = result.scalar_one_or_none()
        if not cfg:
            raise HTTPException(404, "Skill not found")
        await sess.delete(cfg)
        await sess.commit()
    return {"status": "deleted"}
