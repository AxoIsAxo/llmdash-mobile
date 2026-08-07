"""Runtime scheduler for extraction + consolidation.

Triggers (all runtime-driven, never model-driven):
- turn threshold: after a chat turn finishes with >= EXTRACT_MIN_TURNS new
  inbox items, extraction runs immediately (debounced per user);
- idle: the background loop extracts any user whose inbox is stale for >=
  EXTRACT_IDLE_SECONDS;
- safety net: a background loop every EXTRACT_LOOP_SECONDS, plus a full
  consolidate pass every CONSOLIDATE_LOOP_SECONDS;
- shutdown: ``flush_all()`` runs a bounded best-effort extraction for every
  user with a pending inbox (the "session end" pass).

Extraction consumes inbox files (moves them to archive/) so every item is
processed exactly once. Persona deltas are only *queued* here — consolidation
applies them, keeping the working set stable within a session.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

import yaml

from . import config as mem_cfg
from .extract import (
    Extractor,
    ExtractionResult,
    LLMExtractor,
    RuleExtractor,
    Turn,
    new_atom_id,
    new_scenario_id,
    normalize_atom_text,
)
from .store import Store, slugify

logger = logging.getLogger(__name__)


def _read_turn_file(path) -> Optional[Turn]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    parts = text.split("---", 2)
    if len(parts) != 3:
        return None
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        meta = {}
    return Turn(
        source_id=path.stem,
        ts=str(meta.get("ts", "")),
        role=str(meta.get("role", "user")),
        content=parts[2].strip(),
    )


def chunk_turns(turns: list[Turn], max_chars: int = mem_cfg.MAX_BATCH_CHARS) -> list[list[Turn]]:
    batches: list[list[Turn]] = []
    current: list[Turn] = []
    size = 0
    for turn in turns:
        if current and size + len(turn.content) > max_chars:
            batches.append(current)
            current = []
            size = 0
        current.append(turn)
        size += len(turn.content)
    if current:
        batches.append(current)
    return batches


async def resolve_extract_model(user_id: int):
    """Pick (provider, model_config) for extraction: env override -> user's
    most recently used model -> first enabled chat model -> None (rules)."""
    from ..ai import get_provider
    from ..config import settings
    from ..database import Conversation, ModelConfig, async_session
    from sqlalchemy import select

    name = (getattr(settings, "memory_extract_model", "") or "").strip()
    async with async_session() as sess:
        mc = None
        if name:
            r = await sess.execute(select(ModelConfig).where(ModelConfig.model_name == name))
            mc = r.scalars().first()
            if not mc:
                r = await sess.execute(select(ModelConfig).where(ModelConfig.model_name.contains(name)))
                mc = r.scalars().first()
        else:
            r = await sess.execute(
                select(ModelConfig)
                .join(Conversation, Conversation.model_id == ModelConfig.id)
                .where(Conversation.user_id == user_id)
                .order_by(Conversation.updated_at.desc())
            )
            mc = r.scalars().first()
        if not mc:
            r = await sess.execute(
                select(ModelConfig)
                .where(ModelConfig.enabled.is_(True))
                .order_by(ModelConfig.id)
            )
            mc = r.scalars().first()
        if not mc:
            return None
        provider = get_provider(mc.provider)
        if not provider:
            return None
        return provider, mc


class MemoryScheduler:
    """Per-process orchestrator; single instance started in the app lifespan."""

    def __init__(self, store: Store, enabled: bool = True):
        self.store = store
        self.enabled = enabled
        self._running: set[int] = set()
        self._last_consolidate: dict[int, float] = {}
        self._loop_task: Optional[asyncio.Task] = None
        self._shutdown = False

    # --- lifecycle ---------------------------------------------------------
    def start(self) -> asyncio.Task:
        if self._loop_task is None:
            self._loop_task = asyncio.create_task(self._background_loop())
        return self._loop_task

    async def stop(self) -> None:
        self._shutdown = True
        if self._loop_task is not None:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except (asyncio.CancelledError, Exception):
                pass
            self._loop_task = None

    async def flush_all(self) -> None:
        """Bounded session-end extraction for every user with pending inbox."""
        if not self.enabled:
            return
        for user_id in self.store.users():
            if self.store.count_inbox(user_id) == 0:
                continue
            try:
                await asyncio.wait_for(self._extract_user(user_id), timeout=30)
            except (asyncio.TimeoutError, Exception):
                logger.warning("memory: shutdown flush for user %s incomplete — inbox left for next boot", user_id)

    # --- triggers ----------------------------------------------------------
    def on_chat_finished(self, user_id: int) -> None:
        """Called (fire-and-forget) from the chat finalize path."""
        if not self.enabled or self._shutdown:
            return
        try:
            if self.store.count_inbox(user_id) >= mem_cfg.EXTRACT_MIN_TURNS:
                asyncio.create_task(self._extract_user(user_id))
        except Exception as exc:
            logger.warning("memory: extraction trigger failed: %s", exc)

    async def _background_loop(self) -> None:
        while not self._shutdown:
            await asyncio.sleep(mem_cfg.EXTRACT_LOOP_SECONDS)
            try:
                await self._extract_due_users()
            except Exception as exc:
                logger.warning("memory: background extraction pass failed: %s", exc)
            try:
                await self._maybe_consolidate()
            except Exception as exc:
                logger.warning("memory: background consolidate failed: %s", exc)

    async def _extract_due_users(self) -> None:
        if not self.enabled:
            return
        for user_id in self.store.users():
            count = self.store.count_inbox(user_id)
            if count == 0:
                continue
            if count >= mem_cfg.EXTRACT_MIN_TURNS:
                await self._extract_user(user_id)
                continue
            last = self.store.last_inbox_ts(user_id)
            if last is not None:
                idle = (datetime.now(timezone.utc) - last).total_seconds()
                if idle >= mem_cfg.EXTRACT_IDLE_SECONDS:
                    await self._extract_user(user_id)

    async def _maybe_consolidate(self) -> None:
        from .consolidate import consolidate

        for user_id in self.store.users():
            last = self._last_consolidate.get(user_id) or 0.0
            if time.monotonic() - last < mem_cfg.CONSOLIDATE_LOOP_SECONDS:
                continue
            self._last_consolidate[user_id] = time.monotonic()
            try:
                await asyncio.wait_for(asyncio.to_thread(consolidate, self.store, user_id), timeout=60)
            except Exception as exc:
                logger.warning("memory: consolidate user %s failed: %s", user_id, exc)

    # --- extraction --------------------------------------------------------
    async def _extract_user(self, user_id: int) -> None:
        if user_id in self._running:
            return
        self._running.add(user_id)
        try:
            files = self.store.list_inbox(user_id)
            if not files:
                return
            turns = [t for t in (_read_turn_file(f) for f in files) if t is not None]
            if not turns:
                self.store.archive_inbox(user_id, files)
                return
            extractor = await self._make_extractor(user_id)
            results: list[ExtractionResult] = []
            for batch in chunk_turns(turns):
                results.append(await extractor.extract(user_id, batch))
            report = apply_extraction(self.store, user_id, results, turns)
            self.store.append_log(user_id, "extract", report)
            self.store.archive_inbox(user_id, files)
            logger.info("memory: user %s — %s", user_id, report)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("memory: extraction for user %s failed: %s", user_id, exc)
        finally:
            self._running.discard(user_id)

    async def _make_extractor(self, user_id: int) -> Extractor:
        try:
            resolved = await resolve_extract_model(user_id)
        except Exception as exc:
            logger.debug("memory: extractor model resolution failed (%s) — rules", exc)
            resolved = None
        if resolved is None:
            return RuleExtractor()
        provider, mc = resolved
        return LLMExtractor(provider, mc, getattr(mc, "model_name", ""))


def apply_extraction(
    store: Store,
    user_id: int,
    results: list[ExtractionResult],
    turns: list[Turn],
) -> str:
    """Write extraction results into the store: atoms -> entity pages,
    scenarios -> scenario pages, persona deltas -> queued (consolidation
    applies them). Returns a one-line report for log.md."""
    with store.scores_lock(user_id):
        return _apply_extraction_locked(store, user_id, results, turns)


def _apply_extraction_locked(
    store: Store,
    user_id: int,
    results: list[ExtractionResult],
    turns: list[Turn],
) -> str:
    scores = store.read_scores(user_id)
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")

    n_atoms = n_scenarios = n_deltas = 0

    # --- atoms: dedupe by normalized text, append new bullets to pages ------
    existing = {normalize_atom_text(a.get("text", "")): (aid, a) for aid, a in scores.get("atoms", {}).items()}
    by_entity: dict[str, list[tuple[str, dict]]] = {}

    def _page_for_entity(entity: str) -> str:
        return f"pages/entities/{slugify(entity, 'general')}.md"

    for result in results:
        for atom in result.atoms:
            norm = normalize_atom_text(atom.get("text", ""))
            if not norm:
                continue
            dup = existing.get(norm)
            if dup:
                _aid, existing_atom = dup
                sources = set(existing_atom.get("source_ids", [])) | {atom.get("source_id", "")}
                sources.discard("")
                existing_atom["source_ids"] = sorted(sources)
                existing_atom["confidence"] = max(float(existing_atom.get("confidence", 0.5)), float(atom.get("confidence", 0.5)))
                # Promotion rule (v1): a single confident statement is enough.
                existing_atom["confirmed"] = bool(
                    existing_atom.get("confirmed", False)
                    or float(existing_atom.get("confidence", 0)) >= mem_cfg.CONFIDENCE_PROMOTE
                )
                existing_atom["last_access"] = now.isoformat()
                continue
            aid = new_atom_id()
            entity = atom.get("entity", "") or "general"
            entry = {
                "id": aid,
                "text": atom.get("text", ""),
                "entity": entity,
                "kind": atom.get("kind", "fact"),
                "salience": float(atom.get("salience", 0.5)),
                "importance": float(atom.get("salience", 0.5)),
                "confidence": float(atom.get("confidence", 0.5)),
                "confirmed": bool(float(atom.get("confidence", 0.5)) >= mem_cfg.CONFIDENCE_PROMOTE),
                "freq": 0,
                "tags": atom.get("tags", []),
                "source_ids": [atom.get("source_id", "")] if atom.get("source_id") else [],
                "created": now.isoformat(),
                "last_access": now.isoformat(),
                "page": _page_for_entity(entity),
                "links": [],
            }
            scores["atoms"][aid] = entry
            existing[norm] = (aid, entry)
            by_entity.setdefault(entity, []).append((aid, entry))
            n_atoms += 1

    # append new bullets to entity pages (existing pages keep their bullets)
    for entity, entries in by_entity.items():
        rel = _page_for_entity(entity)
        page = store.read_page(user_id, rel) or _new_entity_page(entity, today)
        if not store.read_page(user_id, rel):
            store.write_page(user_id, rel, page)
        bullets = "\n".join(f"- `{aid}` {a.get('text', '')}" for aid, a in entries)
        if not page.rstrip().endswith("## Facts"):
            page = page.rstrip() + "\n"
        store.write_page(user_id, rel, page + bullets + "\n")
        # update source_count on the page frontmatter (best-effort)
        _bump_source_count(store, user_id, rel)

    # --- scenarios ----------------------------------------------------------
    existing_sc = {normalize_atom_text(s.get("title", "")): sid for sid, s in scores.get("scenarios", {}).items()}
    for result in results:
        for sc in result.scenarios:
            norm = normalize_atom_text(sc.get("title", ""))
            if norm and norm in existing_sc:
                continue
            sid = new_scenario_id()
            rel = f"pages/scenarios/{slugify(sc.get('title', 'scenario'))}.md"
            body = (
                "---\n"
                f"tags: [scenario, l2{', ' + ', '.join(sc.get('tags', [])) if sc.get('tags') else ''}]\n"
                f"date: {today}\n"
                "source_count: 1\n"
                "---\n\n"
                f"# {sc.get('title', '')}\n\n"
                f"{sc.get('summary', '')}\n"
            )
            store.write_page(user_id, rel, body)
            scores["scenarios"][sid] = {
                "id": sid,
                "title": sc.get("title", ""),
                "summary": sc.get("summary", ""),
                "tags": sc.get("tags", []),
                "page": rel.replace("pages/", ""),
                "created": now.isoformat(),
                "last_access": now.isoformat(),
                "freq": 0,
            }
            if norm:
                existing_sc[norm] = sid
            n_scenarios += 1

    # --- persona deltas: queue only (consolidation applies them) -------------
    for result in results:
        for pd in result.persona_deltas:
            scores.setdefault("persona_deltas", []).append(
                {"text": pd.get("text", ""), "kind": pd.get("kind", "preference"), "salience": float(pd.get("salience", 0.5)), "ts": now.isoformat()}
            )
            n_deltas += 1

    store.write_scores(user_id, scores)
    return f"extracted {n_atoms} atoms, {n_scenarios} scenarios, {n_deltas} persona deltas from {len(turns)} turns"


def _new_entity_page(entity: str, today: str) -> str:
    title = entity[0].upper() + entity[1:] if entity else "General"
    return (
        "---\n"
        f"tags: [entity, {slugify(entity)}]\n"
        f"date: {today}\n"
        "source_count: 0\n"
        "---\n\n"
        f"# {title}\n\n"
        "## Facts\n"
    )


def _bump_source_count(store: Store, user_id: int, rel: str) -> None:
    """Increment `source_count` in the page frontmatter (best-effort)."""
    page = store.read_page(user_id, rel)
    if not page or not page.startswith("---"):
        return
    parts = page.split("---", 2)
    if len(parts) != 3:
        return
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return
    meta["source_count"] = int(meta.get("source_count", 0)) + 1
    new_fm = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True).rstrip()
    store.write_page(user_id, rel, f"---\n{new_fm}\n---{parts[2]}")
