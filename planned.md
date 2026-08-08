# LLMDash — Planned Features

Planned capabilities, in rough priority order. Each entry sketches the goal, the technical
approach (mapped to the current codebase), and the safety guardrails that must ship with it.

Status legend: 🔲 planned · 🟡 in design · 🟢 implemented

---

## P1 · Bots (Discord / Telegram style)

**Goal:** Let anyone run **bots** on LLMDash — standalone agents that live on chat platforms
(Discord, Telegram, Matrix, IRC, …) and use LLMDash as their brain: the same multi-model
pipeline, memory, and tool ecosystem (web search, documents, sandbox, Extrovert, Git, …).

- Bots are **normally written in Rust**, but the design must be **language-agnostic** — a bot
  is just a small program that speaks HTTP to LLMDash, so it can be written in Rust, Python,
  Node, Go, or anything else.
- **Ingestion:** platform adapters (webhooks for Discord/Telegram, polling for IRC/Matrix).
  Events are normalized into `ChatRequest`-shaped payloads (`backend/app/main.py` chat path).
- **Dispatch:** a bot has its own conversation per channel/user; reuse the existing SSE stream
  endpoint with a bot token instead of a user token.
- **Identity:** bot accounts (role `bot`) that cannot log into the web UI; per-bot API tokens.
- **Lifecycle:** register bots via UI/API, per-bot model + tools + system-prompt override,
  rate limits, kill switch.

**Safety:** bots only get the tools their owner grants; `run_command` and other write tools are
opt-in per bot; per-bot daily token budget (reuse `token_usage_log`); bots can never act on
account endpoints (no `/api/auth`, no user management).

**Effort:** 🔲 ~2–3 weeks (platform adapters + bot accounts + dispatch).

---

## P2 · Agentic capabilities over Extrovert

**Goal:** The AI can **act on your Extrovert account** — post, reply, like, follow, DM — not
just log in with it (current integration is OIDC login only, `backend/app/extrovert_auth.py`).

- **OAuth:** extend the authorize scope beyond `openid profile` to `write` / `follow` /
  `media.write` / `write:direct` (Extrovert's discovery doc already advertises these).
- **Tokens:** store `access_token` / `refresh_token` / expiry on `User` (new columns, same
  `ALTER TABLE` migration pattern as `database.py:259`); refresh via the provider's
  `refresh_token` grant; revoke via `/api/v1/oauth/revoke`.
- **Skills:** new `Skill` subclasses in `backend/app/skills/builtins/` — `extrovert_post`,
  `extrovert_follow`, `extrovert_dm`, `extrovert_home`, … each reading the user's token from
  `_current_user` (context already passed at `main.py:1731`).
- **Open question (blocker):** the write API is undocumented and unconfirmed — needs one
  session reverse-engineering the SPA's network calls with a real account before skills can be
  built.

**Safety:** per-user opt-in consent (never silently request write scopes at login), per-action
confirmation for posts/DMs, rate limits, and a kill switch that revokes the token.

**Effort:** 🟡 ~1 week after the API surface is confirmed (½–1 day if it turns out Mastodon-compatible).

---

## P3 · Agentic capabilities over Obsidian

**Goal:** The AI can **read and write your Obsidian vault** — create/edit notes, link and tag,
query by content, maintain MOCs — with the vault as a first-class tool.

- LLMDash already treats `data/memory/<user_id>/` as an Obsidian-style wiki (`README.md`:
  "open that folder as an Obsidian vault"); extend the same markdown-native approach to a real
  user vault.
- **Vault access:** the vault lives on the user's machine, LLMDash on the VPS — so sync is the
  core problem: git-backed vaults (clone/pull/push, conflicts resolved before write), the
  Obsidian Local REST API plugin on the user's machine, or a mounted volume for self-hosters.
- **Skills:** `vault_read` / `vault_write` / `vault_search` (reuse `scores.json`-style retrieval
  or a lightweight index), with markdown linting on write.
- **Mode:** the model writes notes explicitly when asked ("save this as a note"), never
  silently — same discipline as the existing `edit_document` policy.

**Safety:** vault scope allowlist (only the configured vault root, path-validated, like
`serve_upload`), optional git-backed history for rollback, and an explicit "AI may write to my
vault" toggle.

**Effort:** 🔲 ~1 week (sync adapter + 3 skills + vault config UI).

---

## P4 · Agentic capabilities on your VPS (safe remote ops)

**Goal:** Let the AI **operate the server safely** — inspect, deploy, restart, read logs,
manage containers/files — without handing it a root shell.

- **Custom client (recommended):** a small agent daemon installed on the VPS (single static
  binary, Rust or Go) that **connects out** to LLMDash over a persistent authenticated channel
  and pulls/executes jobs. No SSH keys held by the web app; LLMDash never touches the VPS
  directly.
- **Declarative, not arbitrary:** the daemon exposes a narrow capability list (e.g.
  `service.status`, `service.restart`, `logs.tail`, `deploy.git`, `file.read <allowlist>`),
  each with its own permission flag. No free-form `sh -c`.
- **Approval gates:** admin-configurable — sensitive actions (restart prod, deploy) require an
  explicit approve button in the chat UI before the daemon executes.
- **Audit:** every action logged (who/what/when/exit code) to `token_usage_log`-style table and
  to the daemon side.

**Safety:** per-capability allowlist, per-user roles (only owner/admin can approve), network
egress allowlist on the daemon, and a physical kill switch (remove the client's enrollment
token). The existing sandbox stays for throwaway work; this is for *real* infrastructure.

**Effort:** 🔲 ~2 weeks (daemon + protocol + capability registry + approval UI).

---

## P5 · Agentic Git access (always as "LLMDash")

**Goal:** The AI can **work in Git repositories** — clone, branch, commit, push, open
MRs/PRs — and every commit is authored by a dedicated bot identity, **never by you**.

- **Identity:** commits/pushes use a fixed author `LLMDash <llmdash@<host>>` (configurable
  name, but never the user's git identity); verified by an audit check that refuses to push
  commits with any other author.
- **Credentials:** per-repo deploy keys / bot tokens stored server-side (like provider API
  keys), scoped to a single repo or a single remote; no user SSH keys in play.
- **Skills:** `git_clone` / `git_status` / `git_diff` / `git_commit` / `git_push` /
  `git_pr`, with the existing sandbox container as the worktree (persists per conversation).
- **Workflow guardrails:** pushes only to explicitly granted remotes; pull requests preferred
  over direct pushes to `main` where the host supports it; destructive ops (`force push`,
  `reset --hard`) blocked or require approval; diffs shown in chat before push for
  review-mode.

**Safety:** repo allowlist, author-enforcement check on every push, and a "read-only repos"
default — write access is per-repo opt-in.

**Effort:** 🔲 ~1 week (git skills + credential store + author enforcement + push review).

---

## Dependencies & sequencing

- **P1 (Bots)** is the natural next layer: once bot accounts exist, P2–P5 capabilities become
  grantable *to bots*, not just to chat users — bots on Discord that can post to Extrovert,
  write to your vault, or open a PR as LLMDash.
- **P2 (Extrovert)** is blocked on confirming the write API; everything else is unblocked.
- **P3 (Obsidian)** and **P5 (Git)** both depend on a generic "external repo/volume" sync
  helper — worth building once, sharing between them.
- **P4 (VPS agent)** is the most invasive; design its protocol first so P1 bots can reuse it
  for long-running bot processes.

No dates attached; this is the backlog, not a commitment.
