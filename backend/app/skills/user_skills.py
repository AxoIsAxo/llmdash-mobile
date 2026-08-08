"""P11 — per-user skill scoping helpers.

`user_skills` rows override the default (enabled) state per user and carry
the user's custom config for a skill. Absence of a row = enabled with no
config (default-on, preserving current behavior).
"""

from __future__ import annotations

import json
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import UserSkill


def _parse_config(raw) -> dict:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


async def get_user_disabled_skills(db: AsyncSession, user_id: int) -> set[str]:
    """Skill names the user explicitly disabled (default = enabled)."""
    result = await db.execute(
        select(UserSkill.skill_name).where(
            UserSkill.user_id == user_id,
            UserSkill.enabled == False,  # noqa: E712
        )
    )
    return {row[0] for row in result.all()}


async def get_user_skill_states(db: AsyncSession, user_id: int) -> dict[str, dict]:
    """skill_name -> {"enabled": bool, "config": dict} for every row."""
    result = await db.execute(select(UserSkill).where(UserSkill.user_id == user_id))
    out = {}
    for row in result.scalars().all():
        out[row.skill_name] = {
            "enabled": row.enabled is not False,
            "config": _parse_config(row.config_json),
        }
    return out


async def get_user_skill_configs(db: AsyncSession, user_id: int) -> dict[str, dict]:
    """skill_name -> parsed custom config (default {})."""
    states = await get_user_skill_states(db, user_id)
    return {name: st["config"] for name, st in states.items()}


async def upsert_user_skill(db: AsyncSession, user_id: int, skill_name: str, **fields) -> UserSkill:
    """Create or update the user's row for a skill (enabled / config_json)."""
    existing = (
        await db.execute(
            select(UserSkill).where(
                UserSkill.user_id == user_id,
                UserSkill.skill_name == skill_name,
            )
        )
    ).scalar_one_or_none()
    if existing:
        for key, value in fields.items():
            setattr(existing, key, value)
        return existing
    row = UserSkill(user_id=user_id, skill_name=skill_name, **fields)
    db.add(row)
    return row
