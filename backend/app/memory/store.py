"""Filesystem store for per-user memory wikis.

Layout (root = ``settings.memory_dir``, default ``data/memory``)::

    data/memory/
    └── <user_id>/
        ├── index.md          # catalog: one line per page
        ├── log.md            # append-only "## [YYYY-MM-DD] action | title"
        ├── schema.md         # conventions doc (bootstrapped)
        ├── scores.json       # disposable retrieval index (regenerable)
        ├── pages/
        │   ├── persona.md    # L3 stable user facts
        │   ├── active.md     # L3 current working set
        │   ├── entities/…    # entity pages (Facts bullets = L1 atoms)
        │   └── scenarios/…   # L2 reusable knowledge blocks
        ├── inbox/            # L0 raw transcripts (one file per message)
        └── archive/          # processed inbox files

Markdown is canonical; ``scores.json`` is a cache that ``consolidate``
rebuilds from the pages. All writes are atomic (tmp + rename). The root is a
best-effort inner git repo whose history excludes ``inbox/`` and ``archive/``.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)

_SCHEMA_MD = """# Memory schema

This directory is a per-user memory wiki (LLM-Wiki pattern), maintained by the
LLMDash memory subsystem. Open this folder as an Obsidian vault to browse it.

## Layers
- L0 — `inbox/` raw transcripts, one file per message (user + assistant).
  Written deterministically by the chat request path; never by the model.
  Processed files are moved to `archive/` by extraction.
- L1 — atoms: single facts (preferences, names, events, decisions,
  constraints). Stored as `- `atom-…` text` bullets under `## Facts` on
  entity pages in `pages/entities/`.
- L2 — scenarios: reusable knowledge blocks in `pages/scenarios/`.
- L3 — persona/active state in `pages/persona.md` and `pages/active.md`
  (always injected into every reply, capped at ~500 chars).

## Conventions
- Every atom has an id `atom-<hex>`, unique per user store.
- Pages carry YAML frontmatter: `tags`, `date`, `source_count`.
- Bullet format is machine-parseable:
  `- `atom-abc123` User prefers dark mode.`
- Superseded atoms keep their bullet, suffixed `[superseded YYYY-MM-DD]`.
- `scores.json` is a disposable cache (salience, frequency, confidence,
  links). `consolidate` rebuilds it from the pages — manual Obsidian edits
  are folded back in.
- `index.md` catalogs every page, one line each. `log.md` is append-only:
  `## [YYYY-MM-DD] action | title` (grep-parseable).
- `inbox/` and `archive/` contain raw private transcripts — they are
  excluded from the inner git history.

## Maintenance
- `consolidate` — dedupe/merge atoms, apply persona deltas, decay salience,
  rebuild `index.md` + `scores.json`, append `log.md`, archive inbox.
- `lint` — health checks: orphans, missing links, contradictions, drift.
- Both run scheduled; trigger manually via
  `python -m app.memory.cli consolidate|lint [--user N|--all]` (from backend/).
"""

_ID_RE = re.compile(r"^- `([a-z0-9-]+)` (.*)$", re.MULTILINE)


class Store:
    """Filesystem access for one memory root (all users under it)."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self._scores_locks: dict[int, threading.Lock] = {}

    def scores_lock(self, user_id: int) -> threading.Lock:
        """Per-user lock serializing scores.json + page read-modify-writes.

        Guards against the last-write-wins race between chat-turn boosts
        (worker threads via ``to_thread``) and extraction/consolidation
        (background loop). Within one process, a user's memory writes are
        serialized; multi-worker deployments are documented as out of scope.
        """
        lock = self._scores_locks.get(user_id)
        if lock is None:
            lock = threading.Lock()
            self._scores_locks[user_id] = lock
        return lock

    # --- paths -------------------------------------------------------------
    def user_dir(self, user_id: int) -> Path:
        return self.root / str(user_id)

    def inbox_dir(self, user_id: int) -> Path:
        return self.user_dir(user_id) / "inbox"

    def archive_dir(self, user_id: int) -> Path:
        return self.user_dir(user_id) / "archive"

    def pages_dir(self, user_id: int) -> Path:
        return self.user_dir(user_id) / "pages"

    def entities_dir(self, user_id: int) -> Path:
        return self.pages_dir(user_id) / "entities"

    def scenarios_dir(self, user_id: int) -> Path:
        return self.pages_dir(user_id) / "scenarios"

    # --- bootstrap ---------------------------------------------------------
    def ensure_user(self, user_id: int) -> Path:
        d = self.user_dir(user_id)
        for sub in ("inbox", "archive", "pages/entities", "pages/scenarios"):
            (d / sub).mkdir(parents=True, exist_ok=True)
        for name, content in (("schema.md", _SCHEMA_MD),):
            f = d / name
            if not f.exists():
                self._atomic_write(f, content)
        if not (d / "index.md").exists():
            self._atomic_write(d / "index.md", "# Memory Index\n\n(empty — extraction will fill this.)\n")
        if not (d / "log.md").exists():
            self._atomic_write(d / "log.md", "# Memory log\n\n")
        if not (d / "scores.json").exists():
            self._atomic_write(d / "scores.json", yaml.safe_dump(_empty_scores(), sort_keys=False, allow_unicode=True))
        self._git_init(d)
        return d

    def users(self) -> list[int]:
        if not self.root.exists():
            return []
        out = []
        for p in self.root.iterdir():
            if p.is_dir() and p.name.isdigit():
                out.append(int(p.name))
        return sorted(out)

    def user_exists(self, user_id: int) -> bool:
        return self.user_dir(user_id).exists()

    # --- inbox -------------------------------------------------------------
    def write_inbox(
        self,
        user_id: int,
        *,
        role: str,
        content: str,
        conversation_id: int,
        model: str,
        important: bool = False,
        status: str = "done",
        ts: Optional[datetime] = None,
    ) -> str:
        """Append one L0 transcript message. Returns the file stem (source id)."""
        d = self.ensure_user(user_id)
        ts = ts or datetime.now(timezone.utc)
        source_id = f"{ts.strftime('%Y%m%dT%H%M%S%f')}Z-{uuid.uuid4().hex[:8]}-{conversation_id}-{role}"
        frontmatter = {
            "ts": ts.isoformat(),
            "user": user_id,
            "conversation": conversation_id,
            "role": role,
            "model": model,
            "important": important,
            "status": status,
        }
        body = (
            "---\n"
            + yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True).rstrip()
            + "\n---\n\n"
            + (content or "")
            + "\n"
        )
        self._atomic_write(d / "inbox" / f"{source_id}.md", body)
        return source_id

    def list_inbox(self, user_id: int) -> list[Path]:
        d = self.inbox_dir(user_id)
        if not d.exists():
            return []
        return sorted(p for p in d.iterdir() if p.suffix == ".md")

    def count_inbox(self, user_id: int) -> int:
        return len(self.list_inbox(user_id))

    def last_inbox_ts(self, user_id: int) -> Optional[datetime]:
        files = self.list_inbox(user_id)
        if not files:
            return None
        newest = files[-1]
        return _parse_source_ts(newest.stem)

    def archive_inbox(self, user_id: int, files: list[Path]) -> None:
        """Move processed inbox files to archive/ (keeps raw transcripts)."""
        arch = self.archive_dir(user_id)
        arch.mkdir(parents=True, exist_ok=True)
        for f in files:
            try:
                os.replace(str(f), str(arch / f.name))
            except OSError:
                logger.warning("memory: failed to archive %s", f)

    # --- scores (disposable retrieval index) -------------------------------
    def read_scores(self, user_id: int) -> dict[str, Any]:
        f = self.user_dir(user_id) / "scores.json"
        if not f.exists():
            return _empty_scores()
        try:
            data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            if not isinstance(data, dict) or not data.get("atoms"):
                data = _empty_scores()
            data.setdefault("scenarios", {})
            data.setdefault("persona_deltas", [])
            return data
        except Exception as exc:  # corrupt cache -> regenerate later
            logger.warning("memory: scores.json unreadable (%s); rebuilding on next consolidate", exc)
            return _empty_scores()

    def write_scores(self, user_id: int, scores: dict[str, Any]) -> None:
        self.ensure_user(user_id)
        self._atomic_write(
            self.user_dir(user_id) / "scores.json",
            yaml.safe_dump(scores, sort_keys=False, allow_unicode=True),
        )

    # --- pages -------------------------------------------------------------
    def read_page(self, user_id: int, rel: str) -> Optional[str]:
        f = self.user_dir(user_id) / rel
        if not f.exists():
            return None
        return f.read_text(encoding="utf-8")

    def write_page(self, user_id: int, rel: str, content: str) -> None:
        f = self.user_dir(user_id) / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(f, content)

    def list_pages(self, user_id: int) -> list[Path]:
        pages = self.pages_dir(user_id)
        if not pages.exists():
            return []
        return sorted(p for p in pages.rglob("*.md"))

    def page_rel(self, user_id: int, p: Path) -> str:
        return str(p.relative_to(self.user_dir(user_id)))

    def read_index(self, user_id: int) -> str:
        f = self.user_dir(user_id) / "index.md"
        return f.read_text(encoding="utf-8") if f.exists() else ""

    def write_index(self, user_id: int, content: str) -> None:
        self._atomic_write(self.user_dir(user_id) / "index.md", content)

    def append_log(self, user_id: int, action: str, title: str, date: Optional[str] = None) -> None:
        f = self.user_dir(user_id) / "log.md"
        date = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        line = f"## [{date}] {action} | {title}\n"
        with open(f, "a", encoding="utf-8") as fh:
            fh.write(line)

    def read_log(self, user_id: int) -> str:
        f = self.user_dir(user_id) / "log.md"
        return f.read_text(encoding="utf-8") if f.exists() else ""

    # --- wipe --------------------------------------------------------------
    def reset_user(self, user_id: int) -> bool:
        """Delete a user's whole memory store (CLI `memory reset`)."""
        d = self.user_dir(user_id)
        if not d.exists():
            return False
        shutil.rmtree(d, ignore_errors=True)
        self._git_commit(user_id, f"reset user {user_id} (memory wiped)")
        return True

    # --- inner git (best effort) -------------------------------------------
    def _git_init(self, user_dir: Path) -> None:
        marker = user_dir / ".git-inited"
        if marker.exists():
            return
        try:
            subprocess.run(
                ["git", "init", "-q", str(user_dir)],
                check=True, capture_output=True, timeout=10,
            )
            (user_dir / ".gitignore").write_text("inbox/\narchive/\n", encoding="utf-8")
            # Initial commit of the compiled wiki (schema + index + log).
            subprocess.run(
                ["git", "add", "-A"],
                cwd=user_dir, check=True, capture_output=True, timeout=10,
            )
            subprocess.run(
                ["git", "-c", "user.name=llmdash", "-c", "user.email=memory@llmdash.local",
                 "commit", "-q", "-m", "init memory wiki", "--allow-empty"],
                cwd=user_dir, check=True, capture_output=True, timeout=10,
            )
            marker.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")
        except Exception as exc:
            logger.debug("memory: inner git unavailable (%s) — continuing without history", exc)

    def _git_commit(self, user_id: int, message: str) -> None:
        d = self.user_dir(user_id)
        if not (d / ".git").exists():
            return
        try:
            subprocess.run(["git", "add", "-A"], cwd=d, check=True, capture_output=True, timeout=10)
            subprocess.run(
                ["git", "-c", "user.name=llmdash", "-c", "user.email=memory@llmdash.local",
                 "commit", "-q", "-m", message, "--allow-empty"],
                cwd=d, check=True, capture_output=True, timeout=10,
            )
        except Exception as exc:
            logger.debug("memory: git commit failed (%s)", exc)

    # --- helpers -----------------------------------------------------------
    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex[:8]}")
        tmp.write_text(content, encoding="utf-8")
        os.replace(str(tmp), str(path))


def _empty_scores() -> dict[str, Any]:
    return {
        "version": 1,
        "atoms": {},
        "scenarios": {},
        "persona_deltas": [],
        "last_decay": datetime.now(timezone.utc).isoformat(),
        "last_consolidate": None,
    }


def _parse_source_ts(stem: str) -> Optional[datetime]:
    m = re.match(r"^(\d{8}T\d{6}\d{6})Z-", stem)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y%m%dT%H%M%S%f").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_fact_bullets(text: str) -> list[tuple[str, str]]:
    """Parse ``- `atom-id` fact text`` bullets from a page into (id, text)."""
    return [(m.group(1), m.group(2).strip()) for m in _ID_RE.finditer(text)]


def slugify(text: str, fallback: str = "general") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug[:60] or fallback
