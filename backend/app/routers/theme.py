from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db, ThemeHistory, User
from ..theme import (
    ALLOWED_PRESETS, DEFAULT_SPEC, get_preset,
    theme_spec_to_css, validate_theme_spec,
)
from .auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth/theme", tags=["theme"])

MAX_HISTORY_PER_USER = 50


class ThemePutRequest(BaseModel):
    spec: dict


class ThemeResetRequest(BaseModel):
    preset: str = "default"


def _spec_json(spec: dict) -> str:
    return json.dumps(spec, separators=(",", ":"))


async def _load_user(user_id: int, db: AsyncSession) -> User:
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(404, "User not found")
    return user


def _current_spec(user: User) -> dict:
    if user.theme_spec:
        try:
            return json.loads(user.theme_spec)
        except json.JSONDecodeError:
            pass
    return json.loads(json.dumps(DEFAULT_SPEC))


def _default_css() -> str:
    spec, errors = validate_theme_spec(DEFAULT_SPEC)
    return theme_spec_to_css(spec) if spec and not errors else ""


async def _save_spec(user: User, spec: dict, db: AsyncSession) -> str:
    css = theme_spec_to_css(spec)
    user.theme_spec = _spec_json(spec)
    user.custom_css = css or None
    await db.commit()

    db.add(ThemeHistory(user_id=user.id, spec_json=_spec_json(spec), css=css))
    await db.commit()
    # Keep only the newest MAX_HISTORY_PER_USER versions per user.
    await db.execute(
        delete(ThemeHistory)
        .where(
            ThemeHistory.user_id == user.id,
            ThemeHistory.id.notin_(
                select(ThemeHistory.id)
                .where(ThemeHistory.user_id == user.id)
                .order_by(ThemeHistory.id.desc())
                .limit(MAX_HISTORY_PER_USER)
            ),
        )
    )
    await db.commit()
    return css


@router.get("")
async def get_theme(current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    user = await _load_user(current_user["user_id"], db)
    spec = _current_spec(user)
    css = user.custom_css or _default_css()
    return {
        "spec": spec,
        "css": css,
        "presets": list(ALLOWED_PRESETS),
        "default_preset": DEFAULT_SPEC.get("preset"),
    }


@router.put("")
async def put_theme(req: ThemePutRequest, current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    user = await _load_user(current_user["user_id"], db)
    spec_json = _spec_json(req.spec)
    if len(spec_json) > 200_000:
        raise HTTPException(400, "theme spec too large")
    spec, errors = validate_theme_spec(req.spec)
    if errors:
        raise HTTPException(400, {"errors": errors[:20], "message": "Invalid theme spec"})
    css = await _save_spec(user, spec, db)
    return {"spec": spec, "css": css}


@router.post("/reset")
async def reset_theme(req: ThemeResetRequest, current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    user = await _load_user(current_user["user_id"], db)
    preset_spec = get_preset(req.preset)
    if preset_spec is None:
        raise HTTPException(400, f"unknown preset '{req.preset}'. Available: {', '.join(ALLOWED_PRESETS)}")
    spec, errors = validate_theme_spec(preset_spec)
    if errors:
        raise HTTPException(400, {"errors": errors[:20], "message": "Invalid preset spec"})
    css = await _save_spec(user, spec, db)
    return {"spec": spec, "css": css}


@router.get("/history")
async def theme_history(current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(ThemeHistory)
        .where(ThemeHistory.user_id == current_user["user_id"])
        .order_by(ThemeHistory.id.desc())
        .limit(MAX_HISTORY_PER_USER)
    )
    rows = result.scalars().all()
    return [
        {
            "id": h.id,
            "created_at": h.created_at.isoformat() if h.created_at else "",
            "spec": json.loads(h.spec_json) if h.spec_json else None,
            "css": h.css,
        }
        for h in rows
    ]


@router.post("/restore/{history_id}")
async def restore_theme(history_id: int, current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    user = await _load_user(current_user["user_id"], db)
    result = await db.execute(
        select(ThemeHistory).where(
            ThemeHistory.id == history_id,
            ThemeHistory.user_id == current_user["user_id"],
        )
    )
    history = result.scalar_one_or_none()
    if not history or not history.spec_json:
        raise HTTPException(404, "Theme version not found")
    try:
        spec = json.loads(history.spec_json)
    except json.JSONDecodeError:
        raise HTTPException(500, "Stored theme spec is corrupt")
    spec, errors = validate_theme_spec(spec)
    if errors:
        raise HTTPException(500, {"errors": errors[:20], "message": "Stored theme spec is invalid"})
    css = await _save_spec(user, spec, db)
    return {"spec": spec, "css": css}
