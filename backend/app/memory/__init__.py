"""Cross-session memory for LLMDash.

Per-user, LLM-wiki-style markdown store under ``data/memory/<user_id>/``.

Layers
------
- L0 capture   : deterministic transcript capture into ``inbox/`` (no LLM).
- Extraction   : scheduled pass distills inbox turns -> L1 atoms, L2
                 scenarios, L3 persona deltas (runtime-driven, LLM-assisted
                 with a rule-based fallback).
- Storage      : plain markdown pages + a disposable JSON retrieval index.
- Injection    : automatic, budget-capped [MEMORY] block into the system
                 prompt before every reply.
- Consolidation: periodic "sleep" pass (dedupe/merge/decay/archive) + lint.

The model never decides to save anything; capture and retrieval are plumbing.
"""

from .store import Store

__all__ = ["Store"]
__version__ = "1.0.0"
