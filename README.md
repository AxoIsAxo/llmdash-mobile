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

### Mobile App (Android, Capacitor)

A native Android wrapper around the same React SPA. The LLMDash server URL is **baked into the APK at build time** and cannot be changed by the end user at runtime — to ship a build for a different server, fork this repo and rebuild.

#### Prerequisites

- Node.js 22+ and npm
- JDK 17 or newer
- Android SDK with platform `android-35` and `build-tools;35.0.0`
- `ANDROID_HOME` exported and `cmdline-tools/latest/bin` on `PATH`
- An Android device or emulator for testing

#### One-time setup

```bash
cd frontend
npm install
npm run mobile:setup        # scaffolds frontend/android/ (only needed once)
```

#### Build a debug APK

```bash
cd frontend
npm run mobile:build:debug
```

The prebuild step will interactively prompt for the LLMDash server URL (e.g. `https://ai.redforged.eu/`) and write it to `frontend/.env`. Subsequent builds reuse the value from `.env` (or from the `LLMDASH_SERVER_URL` env var). The APK lands at:

```
frontend/android/app/build/outputs/apk/debug/app-debug.apk
```

Install it on a connected device with:

```bash
adb install -r frontend/android/app/build/outputs/apk/debug/app-debug.apk
```

#### Building for your own server

Edit `.env` at the repo root and set `LLMDASH_SERVER_URL` to your instance, then rebuild:

```bash
echo "LLMDASH_SERVER_URL=https://llmdash.example.com/" > ../.env
npm run mobile:build:debug
```

The build fails fast (with a clear error) if `LLMDASH_SERVER_URL` is not set and stdin is not a TTY.

#### Building via CI

The repository includes a GitHub Actions workflow at `.github/workflows/build-mobile.yml` that builds the debug APK on demand:

1. Push this repo (or a fork) to GitHub
2. Go to **Actions → Build Mobile APK → Run workflow**
3. Enter your LLMDash server URL
4. Download the `llmdash-debug-apk` artifact from the completed run

#### Customizing the icon and splash

Drop a `frontend/resources/icon.png` (1024×1024) and `frontend/resources/splash.png` (2732×2732) and run:

```bash
cd frontend
npm run mobile:icons
```

This regenerates all density-specific icons and splash drawables via `@capacitor/assets`.

#### Local development against a running backend

The easiest setup is to point `LLMDASH_SERVER_URL` at `http://10.0.2.2:8000/` (Android emulator's view of the host) while you have the backend running on your machine.

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
