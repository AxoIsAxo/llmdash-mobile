"""Per-user memory API — every authenticated user can view and manage their
OWN memory from the UI (no server-side data/ access needed). All operations
are scoped to ``current_user["user_id"]`` from the JWT; there is no
cross-user access and no admin-only gating.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException

from .. import config as app_config
from ..memory.consolidate import consolidate, delete_atom
from ..memory.lint import lint
from ..memory.scheduler import MemoryScheduler
from ..memory.store import Store
from .auth import get_current_user, require_entitlement

router = APIRouter(
    prefix="/api/memory",
    tags=["memory"],
    dependencies=[Depends(require_entitlement("memory"))],
)


def _store() -> Store:
    return Store(app_config.settings.memory_dir)


@router.get("/status")
async def memory_status(current_user: dict = Depends(get_current_user)):
    user_id = current_user["user_id"]
    store = _store()
    if not store.user_exists(user_id):
        return _empty_status()
    scores = store.read_scores(user_id)
    atoms = scores.get("atoms", {})
    scenarios = scores.get("scenarios", {})
    log_lines = [ln for ln in store.read_log(user_id).splitlines() if ln.strip()][-20:]
    return {
        "atoms_count": len(atoms),
        "confirmed_count": sum(1 for a in atoms.values() if a.get("confirmed")),
        "scenarios_count": len(scenarios),
        "pages_count": len(store.list_pages(user_id)),
        "inbox_pending": store.count_inbox(user_id),
        "persona": _page_lines(store.read_page(user_id, "pages/persona.md")),
        "active": _page_lines(store.read_page(user_id, "pages/active.md")),
        "atoms": [
            {
                "id": aid,
                "text": a.get("text", ""),
                "entity": a.get("entity", "general"),
                "kind": a.get("kind", "fact"),
                "tags": a.get("tags", []),
                "confidence": a.get("confidence"),
                "confirmed": bool(a.get("confirmed")),
                "salience": a.get("salience"),
                "created": a.get("created"),
                "superseded": a.get("superseded"),
            }
            for aid, a in sorted(atoms.items(), key=lambda kv: kv[1].get("created", ""), reverse=True)
        ],
        "scenarios": [
            {"id": sid, "title": s.get("title", ""), "summary": s.get("summary", ""), "tags": s.get("tags", [])}
            for sid, s in sorted(scenarios.items(), key=lambda kv: kv[1].get("created", ""), reverse=True)
        ],
        "log": log_lines,
    }


@router.delete("/atoms/{atom_id}")
async def memory_delete_atom(atom_id: str, current_user: dict = Depends(get_current_user)):
    user_id = current_user["user_id"]
    if not delete_atom(_store(), user_id, atom_id):
        raise HTTPException(404, "Atom not found")
    return {"status": "deleted", "atom_id": atom_id}


@router.post("/extract")
async def memory_extract(current_user: dict = Depends(get_current_user)):
    user_id = current_user["user_id"]
    store = _store()
    before = store.count_inbox(user_id)
    # Prefer the app's shared worker (honors its enabled flag + per-user
    # _running guard); fall back to a throwaway scheduler otherwise.
    worker = None
    try:
        from ..main import memory_worker as _mw

        worker = _mw
    except Exception:
        worker = None
    if worker is not None and worker.enabled:
        await worker._extract_user(user_id)
    else:
        await MemoryScheduler(store, enabled=True)._extract_user(user_id)
    after = store.count_inbox(user_id)
    return {"processed": before - after, "pending": after}


@router.post("/consolidate")
async def memory_consolidate(current_user: dict = Depends(get_current_user)):
    user_id = current_user["user_id"]
    report = await asyncio.to_thread(consolidate, _store(), user_id)
    return report


@router.post("/lint")
async def memory_lint(current_user: dict = Depends(get_current_user)):
    user_id = current_user["user_id"]
    return {"findings": lint(_store(), user_id)}


@router.delete("")
async def memory_reset(current_user: dict = Depends(get_current_user)):
    """Permanently wipe the current user's entire memory store."""
    user_id = current_user["user_id"]
    wiped = _store().reset_user(user_id)
    return {"status": "reset" if wiped else "no_memory", "user": user_id}


def _empty_status() -> dict:
    return {
        "atoms_count": 0,
        "confirmed_count": 0,
        "scenarios_count": 0,
        "pages_count": 0,
        "inbox_pending": 0,
        "persona": [],
        "active": [],
        "atoms": [],
        "scenarios": [],
        "log": [],
    }


def _page_lines(md: str | None) -> list[str]:
    """L3 page body -> its bullet lines (like the injected working set)."""
    if not md:
        return []
    if md.startswith("---"):
        parts = md.split("---", 2)
        if len(parts) == 3:
            md = parts[2]
    return [ln[2:].strip() for ln in md.splitlines() if ln.startswith("- ") and ln[2:].strip()]
