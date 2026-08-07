"""Consolidation — the periodic "sleep" pass.

Runs scheduled (background loop / CLI / explicit ``consolidate memory`` chat
command). Deterministic file + scores work; no chat-model involvement:

- dedupe + merge near-duplicate atoms (by normalized text / token overlap);
- resolve contradictions (newer wins, older keeps a ``[superseded]`` bullet);
- promote low-confidence atoms that were restated in >= 2 distinct turns;
- apply queued persona deltas to persona.md / active.md (newness-wins);
- decay salience (importance *= DECAY_PER_DAY^days);
- rebuild ``scores.json`` FROM the pages (true cache — manual Obsidian edits
  fold back in; stats preserved by atom id);
- rebuild ``index.md``, append ``log.md``, best-effort inner-git commit.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any, Optional

import yaml

from . import config as mem_cfg
from .extract import normalize_atom_text
from .store import Store, parse_fact_bullets, slugify

logger = logging.getLogger(__name__)


def token_overlap(a: str, b: str) -> float:
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def consolidate(store: Store, user_id: int) -> dict[str, Any]:
    """Run the full sleep pass for one user. Returns a report dict.

    Pure file/scores work (no awaits) — callers run it via ``to_thread`` so
    git subprocess calls and page rewrites never block the event loop.
    """
    report = {
        "user": user_id,
        "atoms_before": 0,
        "atoms_after": 0,
        "merged": 0,
        "superseded": 0,
        "promoted": 0,
        "persona_applied": 0,
    }
    if not store.user_exists(user_id):
        store.ensure_user(user_id)
    with store.scores_lock(user_id):
        _consolidate_locked(store, user_id, report)
    return report


def _consolidate_locked(store: Store, user_id: int, report: dict[str, Any]) -> None:
    scores = store.read_scores(user_id)
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")

    # --- 0. canonical atom set: load FROM the pages --------------------------
    # Pages are the source of truth; scores.json is a disposable cache of
    # stats. Manual Obsidian edits (new/edited/deleted bullets) fold in here.
    atoms = _load_atoms_from_pages(store, user_id, scores.get("atoms", {}))
    if not atoms and scores.get("atoms"):
        # No entity pages at all — keep the cached set rather than wipe on a
        # partial/corrupt state (pages win whenever they exist).
        atoms = dict(scores["atoms"])
    report["atoms_before"] = len(atoms)

    # --- 1. dedupe / merge near-duplicates ----------------------------------
    merged: dict[str, dict[str, Any]] = {}
    by_norm: dict[str, str] = {}
    for aid, atom in atoms.items():
        norm = normalize_atom_text(atom.get("text", ""))
        key = by_norm.get(norm)
        if key is not None:
            _merge_atoms(merged[key], atom)
            report["merged"] += 1
            continue
        # near-duplicate: same entity, high token overlap, not already paired
        for other_id, other in merged.items():
            if (
                other.get("entity") == atom.get("entity", "")
                and token_overlap(norm, normalize_atom_text(other.get("text", ""))) >= mem_cfg.SIMILARITY_MERGE
            ):
                _merge_atoms(other, atom)
                report["merged"] += 1
                break
        else:
            merged[aid] = dict(atom)
            by_norm[norm] = aid
    atoms = merged

    # --- 2. contradictions: newer wins, older gets [superseded] --------------
    items = sorted(atoms.items(), key=lambda kv: kv[1].get("created", ""))
    for i in range(len(items)):
        aid_i, atom_i = items[i]
        if atom_i.get("superseded"):
            continue
        for j in range(i + 1, len(items)):
            aid_j, atom_j = items[j]
            if atom_j.get("superseded"):
                continue
            if atom_i.get("entity") != atom_j.get("entity", ""):
                continue
            if token_overlap(
                normalize_atom_text(atom_i.get("text", "")),
                normalize_atom_text(atom_j.get("text", "")),
            ) < mem_cfg.CONTRADICTION_OVERLAP:
                continue
            if not _conflict(atom_i.get("text", ""), atom_j.get("text", "")):
                continue
            # Newer statement wins; the older keeps a [superseded] marker.
            if atom_j.get("created", "") >= atom_i.get("created", ""):
                older_id, newer_atom = aid_i, atom_j
            else:
                older_id, newer_atom = aid_j, atom_i
            older = atoms[older_id]
            if not older.get("superseded"):
                older["superseded"] = today
                report["superseded"] += 1
            if older_id not in newer_atom.setdefault("links", []):
                newer_atom["links"].append(older_id)

    # --- 3. promote restated low-confidence atoms ----------------------------
    for atom in atoms.values():
        if atom.get("confirmed"):
            continue
        sources = set(atom.get("source_ids", []) or [])
        if float(atom.get("confidence", 0)) >= mem_cfg.CONFIDENCE_PROMOTE or len(sources) >= mem_cfg.PROMOTE_MIN_TURNS:
            atom["confirmed"] = True
            atom["confidence"] = max(float(atom.get("confidence", 0)), mem_cfg.CONFIDENCE_PROMOTE)
            report["promoted"] += 1

    # --- 4. decay ------------------------------------------------------------
    try:
        last_decay = datetime.fromisoformat(scores.get("last_decay", "") or "")
        if last_decay.tzinfo is None:
            last_decay = last_decay.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        last_decay = now
    days = max(0.0, (now - last_decay).total_seconds() / 86400.0)
    factor = mem_cfg.DECAY_PER_DAY ** days
    if days > 0:
        for atom in atoms.values():
            atom["importance"] = float(atom.get("importance", 0.5)) * factor
        for sc in scores.get("scenarios", {}).values():
            sc["importance"] = float(sc.get("importance", 0.5)) * factor
        scores["last_decay"] = now.isoformat()

    scores["atoms"] = atoms

    # --- 5. apply persona deltas (queued by extraction) -----------------------
    persona = _read_body(store.read_page(user_id, "pages/persona.md")) or []
    active = _read_body(store.read_page(user_id, "pages/active.md")) or []
    for delta in scores.get("persona_deltas", []) or []:
        text = (delta.get("text") or "").strip()
        if not text:
            continue
        kind = delta.get("kind", "preference")
        target = active if kind == "state" else persona
        if normalize_atom_text(text) in {normalize_atom_text(line) for line in target}:
            continue  # already known
        target.insert(0, f"- {text}")
        report["persona_applied"] += 1
    scores["persona_deltas"] = []
    if persona:
        store.write_page(user_id, "pages/persona.md", _render_l3("Persona", persona, today, mem_cfg.PERSONA_MAX_CHARS))
    if active:
        store.write_page(user_id, "pages/active.md", _render_l3("Active state", active, today, mem_cfg.ACTIVE_MAX_CHARS))

    # --- 6. rewrite entity pages from atoms ----------------------------------
    by_entity: dict[str, list[str]] = {}
    for aid, atom in atoms.items():
        entity = atom.get("entity", "") or "general"
        by_entity.setdefault(entity, []).append((aid, atom))
    for entity, entries in by_entity.items():
        rel = f"pages/entities/{slugify(entity, 'general')}.md"
        lines = []
        for aid, atom in sorted(entries, key=lambda e: e[1].get("created", "")):
            text = atom.get("text", "")
            if atom.get("superseded"):
                text = f"{text} [superseded {atom['superseded']}]"
            lines.append(f"- `{aid}` {text}")
        body = (
            "---\n"
            f"tags: [entity, {slugify(entity)}]\n"
            f"date: {today}\n"
            f"source_count: {len(entries)}\n"
            "---\n\n"
            f"# {entity[0].upper() + entity[1:] if entity else 'General'}\n\n"
            "## Facts\n"
            + ("\n".join(lines) + "\n" if lines else "(no confirmed facts yet)\n")
            + "\n## Links\n"
            + _render_links(entries, atoms)
        )
        store.write_page(user_id, rel, body)

    # --- 7. scenario pages: refresh frontmatter + ensure index entry ---------
    for sid, sc in scores.get("scenarios", {}).items():
        rel = f"pages/{sc.get('page', '')}"
        if ".." in rel or not rel.startswith("pages/scenarios/"):
            logger.warning("memory: skipping scenario with unsafe page path: %r", sc.get("page"))
            continue
        page = store.read_page(user_id, rel)
        if page is None:
            store.write_page(
                user_id,
                rel,
                "---\n"
                f"tags: [scenario, l2]\n"
                f"date: {today}\n"
                "source_count: 1\n"
                "---\n\n"
                f"# {sc.get('title', '')}\n\n{sc.get('summary', '')}\n",
            )

    # --- 8. persist scores (the merged atom set — loaded from pages above) ---
    scores["atoms"] = atoms
    scores["persona_deltas"] = []
    scores["last_consolidate"] = now.isoformat()
    store.write_scores(user_id, scores)
    report["atoms_after"] = len(atoms)

    # --- 9. index.md + log.md + git ------------------------------------------
    store.write_index(user_id, _render_index(store, user_id))
    store.append_log(
        user_id,
        "consolidate",
        f"merged {report['merged']}, superseded {report['superseded']}, promoted {report['promoted']}, "
        f"persona+{report['persona_applied']}; {report['atoms_before']} -> {report['atoms_after']} atoms",
    )
    store._git_commit(user_id, f"consolidate user {user_id}: {report['atoms_after']} atoms")
    return report


# --- helpers ---------------------------------------------------------------

def delete_atom(store: Store, user_id: int, atom_id: str) -> bool:
    """Permanently delete one atom (scores + its bullet on the entity page).

    User-facing deletion from the memory panel — the bullet is removed from
    the canonical page and the atom dropped from the scores cache; links
    pointing at it are cleaned. Returns False when the atom does not exist.
    """
    with store.scores_lock(user_id):
        scores = store.read_scores(user_id)
        atom = scores.get("atoms", {}).pop(atom_id, None)
        if atom is None:
            return False
        for other in scores.get("atoms", {}).values():
            links = other.get("links", []) or []
            if atom_id in links:
                other["links"] = [l for l in links if l != atom_id]
        store.write_scores(user_id, scores)
        rel = atom.get("page")
        if rel and ".." not in rel and str(rel).startswith("pages/"):
            page = store.read_page(user_id, rel)
            if page:
                marker = f"- `{atom_id}`"
                lines = page.splitlines()
                kept = [
                    ln for ln in lines
                    if not (ln.strip() == marker or ln.strip().startswith(marker + " "))
                ]
                if len(kept) != len(lines):
                    store.write_page(user_id, rel, "\n".join(kept) + "\n")
        store.append_log(user_id, "delete", f"atom {atom_id}: {(atom.get('text') or '')[:80]}")
        return True


def _merge_atoms(target: dict[str, Any], other: dict[str, Any]) -> None:
    target["source_ids"] = sorted(set(target.get("source_ids", []) or []) | set(other.get("source_ids", []) or []))
    target["confidence"] = max(float(target.get("confidence", 0.5)), float(other.get("confidence", 0.5)))
    target["freq"] = int(target.get("freq", 0)) + int(other.get("freq", 0))
    target["importance"] = max(float(target.get("importance", 0.5)), float(other.get("importance", 0.5)))
    target["confirmed"] = bool(target.get("confirmed") or other.get("confirmed"))
    if other.get("created", "") > target.get("created", ""):
        target["text"] = other.get("text", target.get("text", ""))
        target["created"] = other["created"]
    links = set(target.get("links", []) or []) | set(other.get("links", []) or [])
    target["links"] = sorted(links)


def _conflict(a: str, b: str) -> bool:
    """Heuristic contradiction check for two same-entity atoms.

    Caller precondition: the atoms share an entity and their normalized texts
    overlap >= CONTRADICTION_OVERLAP but were NOT merged as near-duplicates
    (overlap < SIMILARITY_MERGE). At that overlap they are the same claim with
    a changed detail — a correction ("dark mode" -> "light mode", "Berlin" ->
    "Munich") — so the newer wins and the older is superseded. Unrelated facts
    about the same entity (low overlap) never reach here.
    """
    na, nb = normalize_atom_text(a), normalize_atom_text(b)
    if len(na) < 6 or len(nb) < 6:
        return False
    return na != nb


def _read_body(md: Optional[str]) -> list[str]:
    if not md:
        return []
    if md.startswith("---"):
        parts = md.split("---", 2)
        if len(parts) == 3:
            md = parts[2]
    return [line[2:].strip() for line in md.splitlines() if line.startswith("- ") and line[2:].strip()]


def _render_l3(title: str, lines: list[str], today: str, max_chars: int) -> str:
    body = "\n".join(f"- {line}" for line in lines)
    if len(body) > max_chars:
        body = body[: max_chars - 1] + "…"
    return (
        "---\n"
        f"tags: [l3]\n"
        f"date: {today}\n"
        "---\n\n"
        f"# {title}\n\n{body}\n"
    )


def _render_links(entries: list[tuple[str, dict]], atoms: dict[str, dict]) -> str:
    linked: set[str] = set()
    for _aid, atom in entries:
        for link in atom.get("links", []) or []:
            target = atoms.get(link)
            if target:
                linked.add(f"[[{target.get('entity', 'general')}]]")
    other = "\n".join(sorted(linked))
    return (other + "\n") if other else "(none yet — add related pages as they emerge)\n"


def _load_atoms_from_pages(store: Store, user_id: int, old_atoms: dict[str, dict]) -> dict[str, dict[str, Any]]:
    """Load the canonical atom set from entity pages, preserving stats by id.

    Pages (the "Facts" bullets) are the source of truth: manual Obsidian
    edits — new bullets, edited text, deleted bullets — fold in here.
    ``scores.json`` supplies the cache: stats (freq, importance, confidence,
    links) are carried over when the text is unchanged; new/changed bullets
    get defaults. Superseded markers on bullets are preserved.
    """
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    atoms: dict[str, dict[str, Any]] = {}

    for page in store.list_pages(user_id):
        rel = store.page_rel(user_id, page)
        if not rel.startswith("pages/entities/"):
            continue
        entity = page.stem
        try:
            text = page.read_text(encoding="utf-8")
        except OSError:
            continue
        for aid, bullet_text in parse_fact_bullets(text):
            superseded = "[superseded" in bullet_text
            text_clean = bullet_text.split(" [superseded")[0].strip()
            prev = old_atoms.get(aid)
            if prev and (prev.get("text", "") or "").strip() == text_clean:
                atom: dict[str, Any] = dict(prev)  # keep stats
            else:
                # Same fact under a new id (manual edit / second extractor
                # pass): treat as a restatement — inherit the fact's stats
                # including its original `created`, so corrections keep
                # chronological ordering.
                prev = next(
                    (pa for pa in old_atoms.values()
                     if normalize_atom_text(pa.get("text", "")) == normalize_atom_text(text_clean)),
                    None,
                )
                if prev:
                    atom = dict(prev)
                else:
                    atom = {
                        "id": aid,
                        "freq": 0,
                        "importance": 0.5,
                        "confidence": 0.7,
                        "confirmed": not superseded,
                        "created": now.isoformat(),
                        "last_access": now.isoformat(),
                        "tags": [entity],
                        "source_ids": [],
                        "links": [],
                    }
            atom["text"] = text_clean
            atom["entity"] = entity
            atom["page"] = rel
            atom["salience"] = float(atom.get("importance", 0.5))
            atom["superseded"] = (today if superseded else (prev.get("superseded") if prev else None)) or None
            atoms[aid] = atom
    return atoms


def _frontmatter(page) -> dict[str, Any]:
    try:
        text = page.read_text(encoding="utf-8")
        if text.startswith("---"):
            parts = text.split("---", 2)
            return yaml.safe_load(parts[1]) or {}
    except (OSError, yaml.YAMLError):
        pass
    return {}


def _render_index(store: Store, user_id: int) -> str:
    lines = ["# Memory Index", ""]
    for page in store.list_pages(user_id):
        rel = store.page_rel(user_id, page)
        meta = _frontmatter(page)
        tags = ",".join(meta.get("tags", []) or [])
        count = meta.get("source_count", "?")
        lines.append(f"- [[{rel}]] | {tags} | sources: {count}")
    lines.append("")
    lines.append("_Generated by consolidate. One line per page._")
    return "\n".join(lines) + "\n"
