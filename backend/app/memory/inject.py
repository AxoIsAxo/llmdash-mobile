"""Retrieval + injection — automatic, invisible, budget-capped.

Before every reply the runtime scores all atoms against the current user
message and builds a ``[MEMORY]`` data block. No tool call, no model
awareness. Hard caps from ``memory.config`` are enforced here.

Scoring::

    rank  = effective_salience(atom) * recall_overlap(query, atom)
    salience = importance * (1 + log1p(freq)) * exp(-age_days/λ) * link_strength
    recall_overlap = |tokens(Q) ∩ tokens(A)| / |tokens(Q)|   (stopwords removed)

Every retrieval boosts ``freq`` and ``last_access`` (spaced-repetition
effect), persisted best-effort.
"""

from __future__ import annotations

import logging
import math
import re
import time
from datetime import datetime, timezone
from typing import Any, Optional

from . import config as mem_cfg
from .store import Store, slugify

logger = logging.getLogger(__name__)

_STOPWORDS = frozenset(
    "a an the and or but if then else for of to in on at by with without from is are was were be been "
    "i you he she it we they me my your our their this that these those what which who whom whose how "
    "when where why do does did done have has had can could will would shall should may might must "
    "not no yes ok okay please about into over under again further then once here there all any both "
    "each few more most other some such only own same so than too very just also now".split()
)

_TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)


def tokenize(text: str) -> set[str]:
    return {t for t in (m.group(0).lower() for m in _TOKEN_RE.finditer(text or "")) if t not in _STOPWORDS and len(t) > 1}


def recall_overlap(query: str, text: str) -> float:
    q = tokenize(query)
    if not q:
        return 0.5  # no query — let salience decide
    a = tokenize(text)
    return len(q & a) / len(q)


def effective_salience(atom: dict[str, Any], now: Optional[datetime] = None) -> float:
    now = now or datetime.now(timezone.utc)
    importance = float(atom.get("importance", 0.5))
    freq = int(atom.get("freq", 1))
    try:
        created = datetime.fromisoformat(atom.get("created", ""))
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age_days = max(0.0, (now - created).total_seconds() / 86400.0)
    except (ValueError, TypeError):
        age_days = 0.0
    recency = math.exp(-age_days / mem_cfg.RECENCY_DAYS)
    link_strength = 1.0 + mem_cfg.LINK_WEIGHT * len(atom.get("links", []))
    return importance * (1.0 + math.log1p(freq)) * recency * link_strength


def _read_l3(store: Store, user_id: int) -> tuple[str, str]:
    persona = store.read_page(user_id, "pages/persona.md") or ""
    active = store.read_page(user_id, "pages/active.md") or ""
    return _strip_frontmatter(persona), _strip_frontmatter(active)


def _strip_frontmatter(md: str) -> str:
    if md.startswith("---"):
        parts = md.split("---", 2)
        if len(parts) == 3:
            return parts[2].strip()
    return md


def _l3_bullets(body: str) -> str:
    """Normalize an L3 page body to its plain bullet lines (drop the
    `# Title` heading and blanks) so the working set renders cleanly."""
    return "\n".join(
        ln[2:].strip() for ln in body.splitlines() if ln.startswith("- ") and ln[2:].strip()
    )


def _working_set(store: Store, user_id: int, scores: dict[str, Any]) -> str:
    """L3 (persona + active) + 2 most recent L2 scenarios — always injected."""
    lines: list[str] = []
    persona, active = _read_l3(store, user_id)
    persona_lines = _l3_bullets(persona)
    active_lines = _l3_bullets(active)
    if persona_lines:
        lines.append("## Persona")
        lines.append(persona_lines)
    if active_lines:
        lines.append("## Active")
        lines.append(active_lines)
    scenarios = sorted(
        scores.get("scenarios", {}).values(),
        key=lambda s: s.get("created", ""),
        reverse=True,
    )
    for sc in scenarios[:2]:
        page = store.read_page(user_id, f"pages/{sc.get('page', '')}") or ""
        title = _page_title(page) or sc.get("title", "Scenario")
        lines.append(f"## Scenario: {title}")
        lines.append(sc.get("summary", "")[:200])
    block = "\n".join(lines).strip()
    return _hard_truncate(block, mem_cfg.WORKING_SET_MAX_CHARS)


def _page_title(md: str) -> str:
    for line in md.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def build_memory_block(
    store: Store,
    user_id: int,
    *,
    query: str = "",
    now: Optional[datetime] = None,
) -> str:
    """Return the [MEMORY] data block (working set + scored atoms + neighbors).

    Empty string when there is nothing to inject. Hard caps: total <=
    MEMORY_DATA_MAX_CHARS, scored <= SCORED_MAX_CHARS, <= MAX_ITEMS items.
    Boosts retrieval stats for the injected atoms (persisted best-effort).
    """
    t0 = time.perf_counter()
    if not store.user_exists(user_id):
        return ""
    scores = store.read_scores(user_id)
    now = now or datetime.now(timezone.utc)

    parts: list[str] = []
    ws = _working_set(store, user_id, scores)
    if ws:
        parts.append(ws)

    # --- scored atoms ------------------------------------------------------
    atoms = scores.get("atoms", {})
    scored: list[tuple[float, str, dict[str, Any]]] = []
    for atom_id, atom in atoms.items():
        if not atom.get("confirmed", False):
            continue  # low-confidence atoms stay out of the working set
        if atom.get("superseded"):
            continue  # replaced by a newer claim
        sal = effective_salience(atom, now)
        overlap = recall_overlap(query, f"{atom.get('text', '')} {' '.join(atom.get('tags', []))}")
        scored.append((sal * overlap, atom_id, atom))

    selected: list[dict[str, Any]] = []
    if query and scored:
        scored.sort(key=lambda t: t[0], reverse=True)
        for _score, atom_id, atom in scored[: mem_cfg.MAX_ITEMS]:
            if _score <= 0:
                break
            selected.append(atom)
            if len(selected) >= mem_cfg.MAX_ITEMS:
                break

    # --- spreading activation: 1-2 linked neighbors if budget remains -------
    neighbors: list[dict[str, Any]] = []
    if selected:
        seen = {id(a) for a in selected}
        for atom in selected:
            for link in atom.get("links", []):
                nbr = atoms.get(link)
                if nbr and id(nbr) not in seen and nbr.get("confirmed", False) and not nbr.get("superseded"):
                    neighbors.append(nbr)
                    seen.add(id(nbr))
                    if len(neighbors) >= mem_cfg.MAX_NEIGHBORS:
                        break
            if len(neighbors) >= mem_cfg.MAX_NEIGHBORS:
                break

    scored_lines: list[str] = []
    for atom in selected:
        scored_lines.append(f"- {atom.get('text', '')} ({', '.join(atom.get('tags', []))})")
    for atom in neighbors:
        scored_lines.append(f"- ↳ {atom.get('text', '')} ({', '.join(atom.get('tags', []))})")
    if scored_lines:
        scored_block = ("## Related\n" + "\n".join(scored_lines)).strip()
        parts.append(_hard_truncate(scored_block, mem_cfg.SCORED_MAX_CHARS))

    block = "\n\n".join(parts)
    block = _hard_truncate(block, mem_cfg.MEMORY_DATA_MAX_CHARS)

    # Boost retrieval stats for everything we just injected. Serialized per
    # user so a concurrent extraction's new atoms/persona deltas are not
    # clobbered by our read-modify-write of the whole scores dict.
    boosted = selected + neighbors
    if boosted:
        try:
            with store.scores_lock(user_id):
                scores = store.read_scores(user_id)
                for atom in boosted:
                    live = scores.get("atoms", {}).get(atom.get("id"))
                    if live is None:
                        continue  # atom superseded/removed meanwhile
                    live["freq"] = int(live.get("freq", 1)) + 1
                    live["last_access"] = now.isoformat()
                store.write_scores(user_id, scores)
        except Exception as exc:
            logger.debug("memory: boost persistence failed (%s)", exc)

    elapsed_ms = (time.perf_counter() - t0) * 1000
    if elapsed_ms > mem_cfg.LOOKUP_TIME_BUDGET_MS:
        logger.debug("memory: injection took %.1fms (budget %dms)", elapsed_ms, mem_cfg.LOOKUP_TIME_BUDGET_MS)
    return block


def _hard_truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= 1:
        return text[:limit]
    return text[: limit - 1] + "…"


def atom_search(store: Store, user_id: int, query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Raw ranked atom search (used by lint/reports and tests)."""
    scores = store.read_scores(user_id)
    ranked = sorted(
        ((effective_salience(a) * recall_overlap(query, a.get("text", "")), a) for a in scores.get("atoms", {}).values()),
        key=lambda t: t[0],
        reverse=True,
    )
    return [a for _s, a in ranked if _s > 0][:limit]
