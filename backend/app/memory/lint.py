"""Lint — deterministic health checks for a user's memory wiki.

Per the LLM-Wiki pattern: contradictions, orphan pages, missing cross-links,
stale claims, and index/log drift. Web-search gap-filling is *flagged* but not
executed (out of scope for v1). Returns findings as structured dicts.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from . import config as mem_cfg
from .consolidate import token_overlap
from .extract import normalize_atom_text
from .store import Store, parse_fact_bullets

_LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


def lint(store: Store, user_id: int) -> list[dict[str, str]]:
    """Return a list of findings: {severity, check, detail}."""
    findings: list[dict[str, str]] = []
    if not store.user_exists(user_id):
        return [{"severity": "error", "check": "store", "detail": f"no memory store for user {user_id}"}]

    scores = store.read_scores(user_id)
    pages = {store.page_rel(user_id, p): p for p in store.list_pages(user_id)}
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # --- index drift --------------------------------------------------------
    index_lines = [ln for ln in store.read_index(user_id).splitlines() if ln.startswith("- [[")]
    indexed = {ln.split("]]", 1)[0][len("- [["):].strip() for ln in index_lines}
    missing_in_index = [rel for rel in pages if rel not in indexed]
    if missing_in_index:
        findings.append({"severity": "warn", "check": "index", "detail": f"pages missing from index.md: {', '.join(sorted(missing_in_index))}"})
    stale_in_index = [rel for rel in indexed if rel not in pages]
    if stale_in_index:
        findings.append({"severity": "warn", "check": "index", "detail": f"index.md lists missing pages: {', '.join(sorted(stale_in_index))}"})

    # --- orphans / cross-links ----------------------------------------------
    L3_PAGES = {"pages/persona.md", "pages/active.md"}  # injected programmatically
    all_text = {rel: p.read_text(encoding="utf-8") for rel, p in pages.items()}
    for rel, text in all_text.items():
        if rel in L3_PAGES:
            continue  # L3 is injected by the runtime, not wiki-linked
        mentioned = {m.group(1) for m in _LINK_RE.finditer(text)}
        for other_rel in all_text:
            if other_rel == rel:
                continue
            if other_rel in text or f"[[{other_rel}]]" in mentioned or other_rel.endswith(".md") and f"[[{other_rel[:-3]}]]" in mentioned:
                break
        else:
            # not linked from anywhere (excluding index.md, which links all)
            if rel != "index.md" and not rel.startswith("pages/scenarios/"):
                findings.append({"severity": "info", "check": "orphan", "detail": f"{rel} is not linked from any other page"})

    # --- missing cross-links: same tag, no link ------------------------------
    tag_map: dict[str, list[str]] = {}
    for rel, text in all_text.items():
        if rel in L3_PAGES:
            continue
        for tgroup in re.findall(r"tags: \[([^\]]+)\]", text):
            for t in [x.strip() for x in tgroup.split(",") if x.strip()]:
                tag_map.setdefault(t, []).append(rel)
    for tag, rels in tag_map.items():
        if len(rels) >= 2:
            linked_pairs = 0
            for a in rels:
                for b in rels:
                    if a != b and (f"[[{b}]]" in all_text[a] or f"[[{b[:-3]}]]" in all_text[a]):
                        linked_pairs += 1
            if linked_pairs == 0:
                findings.append({"severity": "info", "check": "cross-link", "detail": f"pages sharing tag '{tag}' have no links between them: {', '.join(sorted(rels))}"})

    # --- contradictions (superseded markers) --------------------------------
    for rel, text in all_text.items():
        if "[superseded" in text:
            findings.append({"severity": "warn", "check": "contradiction", "detail": f"{rel} contains superseded claims (newer wins, older kept)"})

    # --- stale / unconfirmed atoms ------------------------------------------
    now = datetime.now(timezone.utc)
    stale = []
    for aid, atom in scores.get("atoms", {}).items():
        if atom.get("confirmed"):
            continue
        try:
            created = datetime.fromisoformat(atom.get("created", ""))
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            age = (now - created).total_seconds() / 86400.0
        except (ValueError, TypeError):
            age = 0.0
        if age > mem_cfg.LINT_UNCONFIRMED_MAX_DAYS:
            stale.append(f"{aid} (age {int(age)}d)")
    if stale:
        findings.append({"severity": "info", "check": "stale", "detail": f"unconfirmed atoms older than {mem_cfg.LINT_UNCONFIRMED_MAX_DAYS} days: {', '.join(stale[:5])}"})

    # --- near-duplicate atoms (missed merges) --------------------------------
    atoms = scores.get("atoms", {})
    items = list(atoms.items())
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            aid_i, a = items[i]
            aid_j, b = items[j]
            if a.get("entity") != b.get("entity", ""):
                continue
            ov = token_overlap(normalize_atom_text(a.get("text", "")), normalize_atom_text(b.get("text", "")))
            if ov >= mem_cfg.SIMILARITY_MERGE:
                findings.append({"severity": "warn", "check": "duplicate", "detail": f"near-duplicate atoms {aid_i} / {aid_j} (overlap {ov:.0%})"})

    # --- gap analysis (flagged only; web search is out of scope) -------------
    thin = []
    for rel, p in pages.items():
        if rel.startswith("pages/entities/") and len(parse_fact_bullets(p.read_text(encoding="utf-8"))) == 1:
            thin.append(rel)
    if thin:
        findings.append({"severity": "info", "check": "gap", "detail": f"thin entity pages (1 fact, no links) that a web search could enrich: {', '.join(sorted(thin))}"})

    inbox_backlog = store.count_inbox(user_id)
    if inbox_backlog:
        findings.append({"severity": "info", "check": "backlog", "detail": f"{inbox_backlog} unprocessed inbox items pending extraction"})

    if not findings:
        findings.append({"severity": "ok", "check": "all", "detail": "no issues found"})
    return findings
