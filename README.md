# LLMDash

**Multi-model AI chat platform with tool ecosystem, user management, and Lightning Network subscriptions.**

LLMDash is a self-hosted AI chat interface that connects to multiple LLM providers (DeepSeek, Anthropic Claude, MiniMax, OpenRouter, or any OpenAI-compatible API) and equips them with built-in tools — web search, document editing, HTML preview, and ephemeral sandboxed command execution.

<p align="center">
  <img src="https://img.shields.io/badge/python-3.12-blue" alt="Python">
  <img src="https://img.shields.io/badge/react-19-blue" alt="React">
  <img src="https://img.shields.io/badge/fastapi-latest-green" alt="FastAPI">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
</p>

---

## Features

- **Multi-provider AI** — DeepSeek, Claude, MiniMax, OpenRouter, or any OpenAI-compatible API configured via UI
- **Built-in tools** — AI models can search the web (SearXNG), create/edit .docx/.pdf/.odt documents, render HTML previews, and execute commands in isolated Alpine Docker containers that **persist for the whole conversation** (installs and files survive between tool calls in a chat)
- **Real-time streaming** — Server-Sent Events for token-by-token responses with tool call round-trips
- **Voice input** — Multilingual speech-to-text powered by [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (CTranslate2, 4× faster than the original Whisper), with selectable model size, compute type, and device
- **Conversation branching** — Fork conversations at any message to explore alternative responses
- **User management** — JWT authentication, role-based access (owner/admin/user), IP-based registration limiting
- **Subscription billing** — Lightning Network payments via LNBits with per-plan and per-model token limits
- **Self-hosted** — Docker-native deployment with multi-stage build and docker-compose orchestration
- **Dark-first UI** — Tailwind CSS with dark mode default, custom Markdown renderer with syntax highlighting

---

## Architecture

```
┌──────────────┐     ┌───────────────────┐     ┌─────────────────┐
│  React SPA   │────▶│  FastAPI Backend   │────▶│  AI Providers   │
│  (Vite+TS)   │     │  (Python 3.12)     │     │  DeepSeek/Claude│
│  :5173 dev   │     │  :8000             │     │  MiniMax/OR     │
└──────────────┘     └───────┬───────────┘     └─────────────────┘
                             │
               ┌─────────────┼─────────────┐
               ▼             ▼             ▼
        ┌──────────┐  ┌──────────┐  ┌──────────┐
        │ SearXNG  │  │ Docker   │  │ SQLite   │
        │ Web      │  │ Sandbox  │  │ DB       │
        │ Search   │  │ Alpine   │  │          │
        └──────────┘  └──────────┘  └──────────┘
```

### Tech Stack

| Layer | Technology |
|-------|-----------|
| **Backend** | Python 3.12, FastAPI, SQLAlchemy (async), aiosqlite |
| **Frontend** | React 19, TypeScript, Vite, Tailwind CSS 3 |
| **Database** | SQLite |
| **AI SDKs** | OpenAI Python SDK, Anthropic Python SDK |
| **Infrastructure** | Docker, Docker Compose, SearXNG, LNBits |

---

## Quick Start

### Prerequisites

- Docker and Docker Compose
- At least one AI provider API key

### Setup

```bash
# Clone the repository
git clone https://codeberg.org/axoisaxo/LLMDash.git
cd llmdash

# Copy and edit environment configuration
# NOTE: the backend reads ./data/.env (created automatically on first run —
# API keys can also be set from the Admin Panel → API Keys tab).
cp backend/.env.example data/.env
# Add your API keys to data/.env (or use the Admin Panel)

# Start the application
docker compose up -d
```

The application will be available at `http://localhost:8000`.

### First Run

1. Open `http://localhost:8000` in your browser
2. Create the owner account via the setup wizard
3. Navigate to **Admin Panel** to configure AI providers and models
4. Start chatting!

---

## Configuration

### Environment Variables

The app reads `data/.env` (next to the SQLite database). See `backend/.env.example` for a template. Keys can also be set from **Admin Panel → API Keys**:

| Variable | Required | Description |
|----------|----------|-------------|
| `DEEPSEEK_API_KEY` | No | DeepSeek API key |
| `ANTHROPIC_API_KEY` | No | Anthropic Claude API key |
| `MINIMAX_API_KEY` | No | MiniMax API key |
| `OPENROUTER_API_KEY` | No | OpenRouter API key |
| `SEARXNG_URL` | No | SearXNG instance URL (default: `http://localhost:8080`; `http://searxng:8080` inside Docker) |
| `DATABASE_PATH` | No | SQLite database path (default: `data/llmdash.db`) |
| `JWT_SECRET` | No | JWT signing secret (auto-generated and persisted to `data/jwt_secret` if omitted) |
| `REGISTRATION_ENABLED` | No | Allow self-registration (`true`/`false`) |
| `IP_ACCOUNT_LIMIT` | No | Max accounts per IP address |
| `LNBITS_URL` | No | LNBits instance URL for Lightning payments |
| `LNBITS_INVOICE_KEY` | No | LNBits invoice key |
| `WHISPER_MODEL` | No | faster-whisper model size: `tiny`, `base`, `small`, `medium`, `large-v3`, `distil-large-v3` (default `small`) |
| `WHISPER_COMPUTE_TYPE` | No | `int8`, `int8_float16`, `float16`, `float32`, `bfloat16`, `int16` (default `int8`) |
| `WHISPER_DEVICE` | No | `auto`, `cpu`, or `cuda` (default `auto`) |
| `WHISPER_LANGUAGE` | No | Force a language code (e.g. `en`, `de`); leave empty for auto-detect |

### Adding AI Models via Admin Panel

1. Go to **Admin Panel → Provider Configs**
2. Add your API key for each provider
3. Go to **Admin Panel → Models**
4. Create model configurations with the desired provider, model name, temperature, and max tokens
5. Use **Auto-Scan Models** to auto-discover available models from configured providers

### Docker Compose Services

The `docker-compose.yml` defines two services:

- **llmdash** — Main application (port 8000), mounts Docker socket for sandbox
- **searxng** — Privacy-respecting meta search engine (internal port 8080)

---

## Development

### Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### Tests

```bash
cd backend
pip install -r requirements-dev.txt
pytest

cd ../frontend
npm test
```

### Frontend

```bash
cd frontend
npm install
npm run dev    # Dev server on :5173, proxies /api to :8000
```

### Production Build

```bash
cd frontend
npm run build    # Outputs to frontend/dist/
```

---

## API Overview

All routes are prefixed with `/api`.

| Group | Routes | Description |
|-------|--------|-------------|
| **Auth** | `POST /login`, `/register`, `/setup` | JWT authentication |
| **Users** | `GET/POST /users`, `PUT/DELETE /users/:id` | User CRUD (admin) |
| **Models** | `GET/POST /models` | Model configuration |
| **Chat** | `POST /chat/stream` | Streaming chat (SSE) |
| **Conversations** | `GET/POST /conversations` | Conversation management |
| **Tools** | `GET /tools` | Available tool definitions |
| **Config** | `GET /config/env`, `/config/status` | Environment and service status |
| **Files** | `GET /files/:filename` | Download generated documents |
| **Subscriptions** | `GET/POST /subscriptions/plans` | Lightning Network billing |

---

## Tools

AI models have access to these tools during chat:

| Tool | Description |
|------|-------------|
| `web_search` | Search the web via SearXNG (Google, DuckDuckGo, Wikipedia, etc.) |
| `edit_document` | Create or edit documents in .docx, .pdf, or .odt format |
| `render_html` | Render HTML/CSS/JS and return a preview |
| `run_command` | Execute shell commands in an Alpine Linux sandbox container that persists for the whole conversation (installs and files stay put) |

---

## Cross-Session Memory

LLMDash keeps a **per-user, LLM-wiki-style memory** in plain markdown under `data/memory/<user_id>/` — open that folder as an Obsidian vault to browse it (it contains only the wiki, no code). Memory is **automatic**: the model never decides to save anything, and nothing is lost between sessions.

```
data/memory/<user_id>/
├── index.md      # catalog: one line per page
├── log.md        # append-only "## [YYYY-MM-DD] action | title"
├── schema.md     # conventions doc (bootstrap)
├── scores.json   # disposable retrieval index (rebuilt from pages)
├── pages/        # persona.md, active.md, entities/*.md, scenarios/*.md
├── inbox/        # L0 raw transcripts (one file per message)
└── archive/      # processed transcripts
```

How the pieces wire together in this tool:

1. **Capture (L0)** — `backend/app/memory/capture.py`. The chat request path itself writes every user message (right after it is persisted, before the model call) and every assistant reply (in the finalize path, before the SSE stream ends) to `inbox/` as timestamped transcripts. Capture is deterministic plumbing: it works even if the model errors, refuses, or is cancelled, and it never blocks or breaks chat (`data/memory/` is gitignored).
2. **Extraction** — `backend/app/memory/extract.py` + `scheduler.py`. A runtime scheduler (turn threshold ≥10, ~5 min idle, 10-min background loop, and a bounded flush at shutdown) distills new inbox items into **L1 atoms** (single facts), **L2 scenarios** (reusable knowledge blocks) and **L3 persona deltas**. It uses your configured providers in one batched call per ~40 KB of transcript (strict extraction-only prompt, every atom carries `source`, `timestamp`, `salience`, `confidence`, `tags`), with a zero-dependency rule-based fallback. Low-confidence atoms are stored but excluded from injection until restated.
3. **Storage** — `backend/app/memory/store.py`. Markdown is canonical; `scores.json` is a cache that consolidation rebuilds from the pages, so manual Obsidian edits fold back in. An inner git repo keeps history of the compiled wiki only — `inbox/` and `archive/` (raw transcripts) are excluded from even that.
4. **Injection** — `backend/app/memory/inject.py`. Before every reply the runtime scores atoms against the current message and appends a `[MEMORY]` block to the system prompt. **Hard caps**: working set (L3 persona/active + 2 recent scenarios) ≤500 chars always injected, scored atoms ≤1400 chars, ≤8 items, total data ≤2000 chars, protocol block ≤600 — context grows by ≤ ~2.6k chars from memory. Scoring is `recency × frequency × importance × link_strength` with recall-overlap keyword matching; every retrieval boosts the atom (spaced repetition); injected items pull 1–2 linked neighbors. No tool call, no model awareness.
5. **Consolidation + lint** — `backend/app/memory/consolidate.py` + `lint.py`. The "sleep" pass (every ~6h in background, or on demand) dedupes/merges atoms, resolves contradictions (newer wins, older marked `[superseded]`), applies persona deltas, decays stale salience, rebuilds `index.md`/`scores.json`, and appends `log.md`. `lint` checks for orphans, missing cross-links, index drift, stale claims and duplicates.

The model's only memory footprint is a ~20-line `# Memory protocol` block (in `backend/app/memory/config.py`): use injected memories silently, never announce them, never decide to save, and report maintenance results when asked.

### Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `MEMORY_DIR` | `data/memory` | Memory store root (per-user subfolders) |
| `MEMORY_ENABLED` | `true` | Kill switch for capture/extraction/injection |
| `MEMORY_EXTRACT_MODEL` | *(empty)* | Model for extraction; empty = auto (user's most recent model, else first enabled) |
| `MEMORY_EXTRACT_MIN_TURNS` | `10` | Turns before a batch extraction triggers |
| `MEMORY_EXTRACT_BATCH_CHARS` | `40000` | Max transcript chars per extraction call |

### Extrovert login (OIDC)

Users can register/log in with their Extrovert account, and an existing account can be **explicitly converted** — there is **no username matching**:

- **First Extrovert login** (no linked account) creates a new account whose username is the Extrovert `preferred_username` prefixed with `@` (e.g. `@userA`) — so a normal `userA` and an Extrovert `@userA` can both exist.
- **Conversion:** logged in? Open **sidebar → Account → Connect with Extrovert** (requires an authenticated user). The callback links *that* account to your Extrovert identity and renames it to `@<extrovert-username>`. From then on, Extrovert login resolves purely by the OIDC `sub` — no usernames involved.
- **Logging in via Extrovert** always resolves by the stored `sub`; a never-linked Extrovert identity creates a new `@`-account (when signup is allowed).

Register an OAuth app in Extrovert (`/settings/developers`) with:

| Field | Value |
|-------|-------|
| Redirect URIs | `https://<your-llmdash-host>/api/auth/extrovert/callback` (exact match) |
| Scopes | `openid profile` |

Then set the env vars:

| Variable | Description |
|----------|-------------|
| `EXTROVERT_CLIENT_ID` | The app's client id |
| `EXTROVERT_CLIENT_SECRET` | Optional — Extrovert supports public clients (`none` auth); PKCE S256 secures the exchange either way. Recommended for a web app, but omit it to use the public-client flow |
| `EXTROVERT_ISSUER` | `https://extrovert.redforged.eu` (default) |
| `EXTROVERT_REDIRECT_URI` | Optional override for the auto-derived callback URL |
| `EXTROVERT_ALLOW_SIGNUP` | `true` (default) — first-time Extrovert logins create a new account |

A "Continue with Extrovert" button appears on the login page.

### Manual maintenance (from `backend/`)

```bash
python -m app.memory.cli status [--user N|--all]
python -m app.memory.cli consolidate [--user N|--all]
python -m app.memory.cli lint [--user N|--all]
python -m app.memory.cli reset --user N --yes   # permanently wipes a user's memory (incl. raw transcripts)
```

In chat, `consolidate memory` and `lint memory` run the operation and inject the report for the assistant to summarize. The inner git history keeps compiled-wiki history; purging that history after a reset requires manual `git filter-branch` inside `data/memory/`.

### Privacy

Raw transcripts in `inbox/`/`archive/` are excluded from the inner git history and the app repo is gitignored for `data/memory/`. Memory is keyed per user id from the JWT — never by client-supplied paths — so one user's memories can never leak into another user's context. The memory worker runs in-process (single uvicorn worker); with `--workers N`, each worker maintains its own scheduler — extraction may run twice on the same inbox, but consolidation dedupes the result and no data is lost.

---

## Database

LLMDash uses SQLite with the following tables:

- `users` — User accounts with roles and token usage tracking
- `model_configs` — AI provider model configurations
- `conversations` — Chat conversations
- `messages` — Individual messages with tool call metadata
- `token_usage_log` — Per-user, per-model token consumption
- `subscription_plans` — Subscription tiers with pricing
- `plan_model_limits` — Per-model token limits per plan
- `user_subscriptions` — Active user subscriptions

---

## Security

- **Sandboxed execution**: The `run_command` tool executes in per-conversation Alpine Docker containers with memory/CPU/PID limits; state persists for the chat and containers are cleaned up when the conversation is deleted, after 30 min idle, or on shutdown
- **Read-only Docker socket**: The container mounts `/var/run/docker.sock` as read-only
- **JWT authentication**: All API routes (except login/setup/registration) require Bearer token
- **Role-based access**: Owner, admin, and user roles with scoped permissions
- **IP rate limiting**: Configurable account limit per IP address
- **Environment isolation**: API keys and secrets stored server-side, never exposed to clients

---

## Subscription & Billing

LLMDash supports Lightning Network payments via LNBits:

1. Admin creates subscription plans with price (in sats), duration, and token limits
2. Per-model token limits can be configured for each plan
3. Users subscribe to plans and receive Lightning invoices
4. Payment status is checked via LNBits API and webhook callbacks
5. Expired subscriptions are automatically cleaned up

---

## License

MIT
