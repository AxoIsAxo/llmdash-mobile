"""Extraction — scheduled distillation of L0 inbox transcripts.

The runtime decides when extraction runs (turn-count / idle / background
loop / shutdown flush); the chat model never invokes it. Two strategies behind
one interface:

- :class:`LLMExtractor` — one batched, non-streaming call per ~40KB of
  transcript via the app's own provider layer. Strict extraction-only rules:
  no invented facts, every atom carries confidence + source turn.
- :class:`RuleExtractor` — zero-dependency fallback (pattern matching),
  weaker atoms, lower confidence so they never enter the working set until
  restated.

Every atom is stored with ``source``, ``timestamp``, ``salience``,
``confidence`` and ``tags``; low-confidence atoms are gated out of injection
until consolidation promotes them.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from . import config as mem_cfg

logger = logging.getLogger(__name__)

VALID_KINDS = ("preference", "name", "event", "decision", "constraint", "fact")
PERSONA_KINDS = ("preference", "identity", "state")

EXTRACTION_SYSTEM_PROMPT = """You are a memory distillation engine. You read raw chat transcripts and emit structured memory entries.

RULES — strict extraction only:
- Extract ONLY facts explicitly stated or directly implied by the USER in the transcript. Never infer from nowhere, never invent, never guess ("user probably wants X" is forbidden).
- atoms: single facts — preferences, names, events, decisions, constraints, deadlines.
- scenarios: reusable knowledge blocks — how a task was solved, what a document or decision says, a workflow the user relies on.
- persona_deltas: stable high-level facts about the user (identity, preferences, active state/goals).
- Every atom needs: text (one sentence), entity (a person/project/thing it is about, or ""), kind (one of preference|name|event|decision|constraint|fact), salience 0-1 (how likely this still matters weeks from now), confidence 0-1 (how explicitly it was stated), tags (2-4 short lowercase tags), and source_turn (the [N] turn number it came from).
- Do NOT emit: small talk, one-off commands, tool output, assistant explanations, or anything not stated by the user.
- Ignore assistant turns that report errors or tool-only rounds (they carry no user facts).
- persona_deltas kind must be one of preference|identity|state. State = active goals/working set (e.g. "user is building X").

Respond with ONLY a JSON object, no commentary:
{"atoms": [{"text": "...", "entity": "", "kind": "preference", "salience": 0.8, "confidence": 0.9, "tags": ["..."], "source_turn": 1}], "scenarios": [{"title": "...", "summary": "...", "tags": ["..."]}], "persona_deltas": [{"text": "...", "kind": "preference", "salience": 0.9}]}"""


@dataclass
class Turn:
    source_id: str
    ts: str
    role: str
    content: str


@dataclass
class ExtractionResult:
    atoms: list[dict[str, Any]] = field(default_factory=list)
    scenarios: list[dict[str, Any]] = field(default_factory=list)
    persona_deltas: list[dict[str, Any]] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.atoms or self.scenarios or self.persona_deltas)


class Extractor(ABC):
    @abstractmethod
    async def extract(self, user_id: int, turns: list[Turn]) -> ExtractionResult:
        ...


class RuleExtractor(Extractor):
    """Deterministic fallback: pattern-matches user turns for explicit facts."""

    _PATTERNS = [
        (re.compile(r"\bmy name is (?P<fact>[A-Za-z][A-Za-z0-9 _-]{1,40})", re.I), "name", "identity", "{}"),
        (re.compile(r"\b(i|we) (?:prefer|like|love|enjoy|favor|favour)\s+(?P<fact>.+?)[.!?]?\s*$", re.I), "preference", "preference", "User prefers {}"),
        (re.compile(r"\b(i|we) (?:use|work with|run|develop|build)\s+(?P<fact>.+?)[.!?]?\s*$", re.I), "fact", "state", "User uses {}"),
        (re.compile(r"\b(?:deadline|due date|due)\s+(?:is|by)\s+(?P<fact>.+?)[.!?]?\s*$", re.I), "constraint", "state", "Deadline is {}"),
        (re.compile(r"\b(i|we) (?:decided|chose|will|need to|must)\s+(?P<fact>.+?)[.!?]?\s*$", re.I), "decision", "state", "User decided to {}"),
        (re.compile(r"\bremember (?:that )?(?P<fact>.+?)[.!?]?\s*$", re.I), "fact", "preference", "User noted: {}"),
    ]

    async def extract(self, user_id: int, turns: list[Turn]) -> ExtractionResult:
        return self.extract_sync(turns)

    def extract_sync(self, turns: list[Turn]) -> ExtractionResult:
        result = ExtractionResult()
        for turn in turns:
            if turn.role != "user":
                continue
            text = turn.content.strip()
            for pattern, kind, persona_kind, fmt in self._PATTERNS:
                for m in pattern.finditer(text):
                    fragment = m.group("fact").strip().rstrip(".")
                    if len(fragment) < 3 or len(fragment) > 200:
                        continue
                    if fmt == "{}":
                        frag = fragment[0].upper() + fragment[1:]
                    elif fragment[0].isupper():
                        frag = fragment[0].lower() + fragment[1:]  # mid-sentence
                    else:
                        frag = fragment
                    fact = fmt.format(frag)
                    result.atoms.append(
                        {
                            "text": fact,
                            "entity": "",
                            "kind": kind,
                            "salience": 0.3,
                            "confidence": 0.5,
                            "tags": [kind],
                            "source_turn": turns.index(turn) + 1,
                        }
                    )
                    result.persona_deltas.append({"text": fact, "kind": persona_kind, "salience": 0.3})
        return result


class LLMExtractor(Extractor):
    """Batched, strict, non-streaming LLM extraction via the app's provider."""

    def __init__(self, provider, model_config, model_name: str):
        self.provider = provider
        self.model_config = model_config
        self.model_name = model_name

    async def extract(self, user_id: int, turns: list[Turn]) -> ExtractionResult:
        transcript = render_transcript(turns)
        user_prompt = (
            f"Distill the following chat transcript (turns numbered [N]; user and assistant "
            f"messages interleaved; current UTC time {datetime.now(timezone.utc).isoformat()}).\n\n"
            f"{transcript}"
        )
        try:
            resp = await self.provider.chat(
                [
                    {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                [],
                self.model_config,
            )
        except Exception as exc:
            logger.warning("memory: LLM extraction failed for user %s (%s) — falling back to rules", user_id, exc)
            return await RuleExtractor().extract(user_id, turns)
        return parse_extraction_json(resp.content or "", turns)


def render_transcript(turns: list[Turn]) -> str:
    """Render turns for the LLM, numbered [N], with per-turn size caps."""
    lines = []
    for i, turn in enumerate(turns, start=1):
        content = _cap_turn(turn.content)
        lines.append(f"[{i}] ({turn.role}, {turn.ts})\n{content}")
    return "\n\n".join(lines)


def _cap_turn(content: str) -> str:
    if len(content) <= mem_cfg.MAX_TURN_CHARS:
        return content
    head = content[: mem_cfg.MAX_TURN_CHARS // 2]
    tail = content[-mem_cfg.MAX_TURN_CHARS // 2 :]
    return f"{head}\n…[truncated]…\n{tail}"


def parse_extraction_json(raw: str, turns: list[Turn]) -> ExtractionResult:
    """Parse the strict JSON response; falls back to rule extraction on failure."""
    try:
        text = raw.strip()
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
            text = re.sub(r"\n?```$", "", text)
        if not text.startswith("{"):
            start, end = text.find("{"), text.rfind("}")
            text = text[start : end + 1]
        data = json.loads(text)
    except (ValueError, json.JSONDecodeError) as exc:
        logger.warning("memory: extractor returned unparseable JSON (%s) — falling back to rules", exc)
        return RuleExtractor().extract_sync(turns)

    result = ExtractionResult()
    for raw_atom in data.get("atoms", []) or []:
        if not isinstance(raw_atom, dict) or not raw_atom.get("text"):
            continue
        kind = raw_atom.get("kind", "fact")
        if kind not in VALID_KINDS:
            kind = "fact"
        try:
            turn_no = int(raw_atom.get("source_turn", 1))
        except (TypeError, ValueError):
            turn_no = 1
        source_id = turns[turn_no - 1].source_id if 1 <= turn_no <= len(turns) else (turns[0].source_id if turns else "")
        result.atoms.append(
            {
                "text": str(raw_atom["text"])[:500],
                "entity": str(raw_atom.get("entity", "") or "")[:120],
                "kind": kind,
                "salience": _clamp01(raw_atom.get("salience", 0.5)),
                "confidence": _clamp01(raw_atom.get("confidence", 0.5)),
                "tags": [str(t)[:24] for t in (raw_atom.get("tags", []) or [])][:5],
                "source_id": source_id,
                "ts": next((t.ts for t in turns if t.source_id == source_id), ""),
            }
        )
    for raw_sc in data.get("scenarios", []) or []:
        if not isinstance(raw_sc, dict) or not raw_sc.get("title"):
            continue
        result.scenarios.append(
            {
                "title": str(raw_sc["title"])[:120],
                "summary": str(raw_sc.get("summary", ""))[:2000],
                "tags": [str(t)[:24] for t in (raw_sc.get("tags", []) or [])][:5],
            }
        )
    for raw_pd in data.get("persona_deltas", []) or []:
        if not isinstance(raw_pd, dict) or not raw_pd.get("text"):
            continue
        pk = raw_pd.get("kind", "preference")
        if pk not in PERSONA_KINDS:
            pk = "preference"
        result.persona_deltas.append(
            {"text": str(raw_pd["text"])[:300], "kind": pk, "salience": _clamp01(raw_pd.get("salience", 0.5))}
        )
    return result


def _clamp01(v: Any) -> float:
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return 0.5


def new_atom_id() -> str:
    return f"atom-{uuid.uuid4().hex[:10]}"


def new_scenario_id() -> str:
    return f"sc-{uuid.uuid4().hex[:10]}"


def normalize_atom_text(text: str) -> str:
    """Normalized form used for dedupe/merge."""
    return re.sub(r"\s+", " ", text.lower().strip().rstrip("."))
