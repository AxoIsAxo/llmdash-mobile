"""Memory subsystem constants and the system-prompt protocol block.

All budget caps live here so the "not more than needed" requirement is a
single source of truth (and testable in one place).
"""

from __future__ import annotations

# --- System prompt footprint ----------------------------------------------
# The ONLY memory text the chat model ever sees. ~600 chars.
MEMORY_PROTOCOL_BLOCK = (
    "# Memory protocol\n"
    "Relevant memories may be injected as [MEMORY] blocks. Treat them as your "
    "own knowledge; use them silently, never announce \"as I recall\". You "
    "never decide to save anything — capture is automatic. If the user "
    "explicitly says to remember something, confirm briefly — it is already "
    "captured and flagged. If injected memories conflict with new "
    "information, trust the newer and mention the conflict once. Memory "
    "maintenance (consolidate, lint) is scheduled and rewrites pages per "
    "schema.md; if the user asks about it, summarize the report injected with "
    "[MEMORY], never invent state."
)

# --- Injection budget caps (hard, enforced in inject.build_memory_block) ---
WORKING_SET_MAX_CHARS = 500   # L3 persona + active + 2 recent L2 scenarios
SCORED_MAX_CHARS = 1400       # ranked L1 atoms (and neighbors)
MEMORY_DATA_MAX_CHARS = 2000  # total [MEMORY] data block (working + scored)
MAX_ITEMS = 8                 # max scored items
MAX_NEIGHBORS = 2             # spreading-activation neighbors
LOOKUP_TIME_BUDGET_MS = 50

# --- Salience model --------------------------------------------------------
RECENCY_DAYS = 14.0           # lambda in exp(-age_days / lambda)
DECAY_PER_DAY = 0.97          # consolidation decay on importance
LINK_WEIGHT = 0.2             # link_strength = 1 + LINK_WEIGHT * n_links

# --- Confirmation gating ---------------------------------------------------
CONFIDENCE_PROMOTE = 0.7      # atoms at/above this may be promoted
PROMOTE_MIN_TURNS = 2         # ...if stated in >= this many distinct turns

# --- Extraction ------------------------------------------------------------
EXTRACT_MIN_TURNS = 10        # backstop threshold for the background loop only
EXTRACT_IDLE_SECONDS = 300    # backstop: loop extracts after this idle
EXTRACT_LOOP_SECONDS = 600    # background safety-net loop
EXTRACT_MAX_TOKENS = 2048     # cap for the per-turn extraction call
MAX_TURN_CHARS = 8000         # per-turn transcript cap (head + tail)
MAX_BATCH_CHARS = 40000       # per-LLM-call transcript cap (split batches)

# --- Consolidation / lint --------------------------------------------------
CONSOLIDATE_LOOP_SECONDS = 21600  # full sleep pass every 6h (background)
PERSONA_MAX_CHARS = 500
ACTIVE_MAX_CHARS = 400
SIMILARITY_MERGE = 0.75       # token overlap for near-duplicate atom merge
CONTRADICTION_OVERLAP = 0.5   # token overlap for contradiction pairing
LINT_UNCONFIRMED_MAX_DAYS = 30

# --- Chat-command interception (anchored; see commands.py) -----------------
MAINTENANCE_COMMAND_RE = r"^(consolidate|lint)\s+memory\.?$"
