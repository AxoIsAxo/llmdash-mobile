# LLMDash Fix Plan

## P0 — Security + Critical Bugs

### P0.1 Password hashing
`database.py:37` SHA-256+salt → `passlib[bcrypt]`

### P0.2 Skill system (replaces tools.py)
Create `backend/app/skills/` with:
- `base.py` — Skill ABC, SkillMetadata, SkillResult
- `registry.py` — SkillRegistry (replaces global `available_tools` dict)
- `loader.py` — filesystem + DB loader for custom skills
- `router.py` — CRUD `/api/skills`
- `models.py` — SkillConfig DB table
- `integration.py` — system prompt builder, skill→tool conversion
- `builtins/` — 10 current tools migrated to Skill subclasses

Delete `tools.py`. Update call sites: `main.py:1374-1378` (tool definitions), `main.py:1483` (execute_tool), `ai.py` (ToolDef→Skill).

### P0.3 "Let me try different sources" loop
4-part fix: (A) system prompt: add failure-handling instructions. (B) prefix tool errors with `__TOOL_ERROR__:`. (C) between tool rounds, discard `accumulated_content` if it contains only filler. (D) add cumulative tool-call counter — abort if >3 consecutive `web_search`/`web_scrape` with no substance.

## P1 — Maintainability

### P1.1 Split main.py (2195 lines)
Extract routers: `models.py`, `conversations.py`, `chat.py`, `documents.py`, `config.py`, `files.py`. Extract shared utilities: `system_prompt.py`, `token_gate.py`, `model_capabilities.py`, `sse.py`.

### P1.2 Deduplicate .env parsing
Create `config_file.py` with `ConfigFileManager.read/write/update`. Replace 4 copies in `main.py:697-734`, `main.py:779-837`, `auth.py:310-339`, `auth.py:348-376`.

### P1.3 Split App.tsx god component
Create hooks: `useChat.ts`, `useConversations.ts`, `useAuth.ts`. Extract duplicated SSE processing from handleSend/resumeGeneration/handleRegenerate into one shared handler.

### P1.4 Unify frontend API layer
Migrate all `fetch()` calls in `api.ts` to use `request<T>()`.

## P2 — Quality

P2.1: Add `reportError()` function, replace empty `catch {}` blocks.
P2.2: Remove redundant `pip install pydantic-settings` from Dockerfile:52.
P2.3: Replace hardcoded `data/documents`, `data/uploads`, `.env` paths with config references.

## P3 — Nice to have

P3.1: Add pytest + vitest.
P3.2: Alembic migrations (replace ad-hoc `_migrate()`).
P3.3: Provider configs in DB (replace `provider_configs.json`).
P3.4: API rate limiting via `slowapi`.

## Phases

1. P0.1 + P0.3 (1-2h)
2. P0.2 skill system (3-5d)
3. P1.1 + P1.2 split main.py + dedup env (2-3d)
4. P1.3 + P1.4 frontend refactor (2-3d)
5. P2 quick wins (1d)
6. P3 testing/migrations/ratelimit (3-5d)
