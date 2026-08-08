"""P9 — fine-grained subscription entitlements.

Every feature/tool/capability is a boolean entitlement on a subscription
plan. Enforcement is server-side first:

- the tool registry hides disallowed skills from the model,
- API endpoints 403 for disallowed features,
- the UI hides/disables locked features (cosmetic only).

Defaults are ALL-ON for every plan (including the seeded Free plan): Free is
the implicit fallback plan for every user without an active subscription —
including the owner of a self-hosted instance — so locking features by
default would break the primary use case. Nothing is silently locked until
an admin explicitly turns a flag off in Admin Panel → Subscriptions.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .database import PlanModelLimit, SubscriptionPlan, UserSubscription

# Every feature key. Keys for PLANNED features (P1/P2/P3/P7/P8/P10/P11/P12)
# exist from day one so nothing new can ship ungated; enforcement lands
# together with each feature.
DEFAULT_ENTITLEMENTS: dict[str, bool] = {
    # --- tools (model-visible) ---
    "web_search": True,             # web_search + web_scrape skills
    "document_editor": True,        # edit_document skill + /api/documents
    "render": True,                 # render_html / render_svg / render_video skills
    "sandbox": True,                # run_command skill
    "git_access": True,             # P4: git_* skills + /api/git
    "theme_editing": True,          # theme/css skills + write endpoints
    # --- capabilities (endpoints + UI) ---
    "image_generation": True,       # /api/chat/image
    "file_upload": True,            # /api/chat/upload
    "voice_input": True,            # /api/chat/transcribe (STT)
    "tts": True,                    # P7 (enforced when built)
    "youtube_previews": True,       # P8 (enforced when built)
    "conversation_branching": True, # /api/conversations/{id}/branch
    "memory": True,                 # memory injection + capture + /api/memory
    # --- planned agentic features (keys exist; enforcement with the feature) ---
    "extrovert_agentic": True,      # P1
    "obsidian_vault": True,         # P2
    "vps_agent": True,              # P3
    "proton_pass": True,            # P10
    "skills_management": True,      # P11
    "skill_marketplace": True,      # P12
}


def _parse_stored(raw) -> dict | None:
    """Plan.entitlements is a JSON string (or None) -> dict (or None)."""
    if not raw:
        return None
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except (ValueError, TypeError):
        return None


def merge_entitlements(raw) -> dict[str, bool]:
    """Full entitlement dict: defaults + stored overrides.

    Stored keys that are not booleans, and unknown keys, are ignored so a
    future code version can never silently enable/disable a feature it does
    not know about.
    """
    out = dict(DEFAULT_ENTITLEMENTS)
    stored = _parse_stored(raw)
    for key, value in (stored or {}).items():
        if key in out and isinstance(value, bool):
            out[key] = value
    return out


async def get_effective_plan(db: AsyncSession, user_id: int) -> SubscriptionPlan | None:
    """The user's active plan, or the Free fallback plan when they have no
    active (non-expired) subscription — same lookup shape as chat_stream."""
    result = await db.execute(
        select(UserSubscription, SubscriptionPlan)
        .join(SubscriptionPlan, UserSubscription.plan_id == SubscriptionPlan.id)
        .where(
            UserSubscription.user_id == user_id,
            UserSubscription.status == "active",
        )
        .where(
            (UserSubscription.expires_at > datetime.now(timezone.utc))
            | (UserSubscription.expires_at.is_(None))
        )
    )
    row = result.first()
    if row:
        return row[1]
    free_result = await db.execute(select(SubscriptionPlan).where(SubscriptionPlan.name == "Free"))
    return free_result.scalar_one_or_none()


async def get_user_entitlements(db: AsyncSession, user_id: int) -> dict[str, bool]:
    """Merged entitlements of the user's effective plan (defaults all-on)."""
    plan = await get_effective_plan(db, user_id)
    return merge_entitlements(plan.entitlements if plan else None)


async def get_denied_model_ids(db: AsyncSession, user_id: int) -> set[int]:
    """Model ids the user's effective plan explicitly denies.

    Absence of a plan_model_limits row means "allowed" (current semantics);
    only rows with allowed=False deny access."""
    plan = await get_effective_plan(db, user_id)
    if plan is None:
        return set()
    result = await db.execute(
        select(PlanModelLimit.model_id).where(
            PlanModelLimit.plan_id == plan.id,
            PlanModelLimit.allowed == False,  # noqa: E712 — SQLite bool is 0/1
        )
    )
    return {row[0] for row in result.all()}
