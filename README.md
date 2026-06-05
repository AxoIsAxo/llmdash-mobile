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
- **Built-in tools** — AI models can search the web (SearXNG), create/edit .docx/.pdf/.odt documents, render HTML previews, and execute commands in isolated Alpine Docker containers
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
cp backend/.env.example backend/.env
# Add your API keys to backend/.env

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

Create `backend/.env` (see `backend/.env.example` for a template):

| Variable | Required | Description |
|----------|----------|-------------|
| `DEEPSEEK_API_KEY` | No | DeepSeek API key |
| `ANTHROPIC_API_KEY` | No | Anthropic Claude API key |
| `MINIMAX_API_KEY` | No | MiniMax API key |
| `OPENROUTER_API_KEY` | No | OpenRouter API key |
| `SEARXNG_URL` | No | SearXNG instance URL (default: `http://searxng:8080`) |
| `DATABASE_PATH` | No | SQLite database path (default: `data/llmdash.db`) |
| `JWT_SECRET` | No | JWT signing secret (auto-generated if omitted) |
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
| `run_command` | Execute shell commands in an ephemeral Alpine Linux Docker container |

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

- **Sandboxed execution**: The `run_command` tool executes in ephemeral Alpine Docker containers with no persistence
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
