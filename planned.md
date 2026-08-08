# LLMDash — Planned Features

Planned capabilities, in rough priority order. Each entry sketches the goal, the technical
approach (mapped to the current codebase), and the safety guardrails that must ship with it.

Status legend: 🔲 planned · 🟡 in design · 🟢 implemented

---

## P1 · Agentic capabilities over Extrovert

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

## P2 · Agentic capabilities over Obsidian

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

## P3 · Agentic capabilities on your VPS (safe remote ops)

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

## P4 · Agentic Git access (always as "LLMDash")

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

**Effort:** 🟢 implemented.

---

## P5 · No forced auto-scroll while the answer is generating

**Goal:** The interface must **stop jumping to the bottom** when an answer is generated —
especially when the user has scrolled up to read earlier messages or review tool output.

- Current behavior: `frontend/src/App.tsx` auto-scrolls to the newest content as
  `content_delta` events stream and again when the message finalizes, yanking the viewport
  down mid-generation.
- **Scroll policy:** only auto-scroll while the user is already "at the bottom" (within a
  small threshold of the end); if the user has scrolled up, leave the viewport alone. Add a
  floating "jump to latest" affordance that appears when the user is not at the bottom and a
  generation is active/completed.
- **Toggle:** a user setting `auto-scroll` (default on) so anyone who *does* want the old
  behavior keeps it. Per-conversation, persisted like the theme/memory preferences.

**Safety:** none — pure viewport behavior. Must not interfere with the SSE stream handling
(`sse.py` / `chat.send` in `api.ts`).

**Effort:** 🔲 ~½ day (scroll-threshold logic + toggle + floating button).

---

## P6 · Tool-call timeline must stay above the answer during streaming

**Goal:** Fix the bug where **tool calls flow to the bottom during generation**: while the
message is being generated, entries like `[ web_search ]` / the search result appear *under*
the accumulating answer text, and only snap back to their correct position (above the answer)
when the message finishes.

- **Cause:** during streaming the frontend renders the growing `content_delta` text block and
  appends `tool_calls` / `tool_start` / `tool_result` events to a separate list that is laid
  out *after* the text; the correct final ordering (thinking → tools → answer) is only applied
  at finalize time (`status: done`), so the in-flight DOM order is wrong.
- **Fix:** render the tool timeline from the streaming events *above* the accumulating text
  container at all times — the same chronological order the `thinking_json` timeline already
  stores (`App.tsx` streaming handlers + the `Message` render path). The model's "thinking →
  searching → answering" flow must be visible above the answer from the first token, not
  after the message completes.

**Safety:** none — pure rendering order. Must not change what gets persisted
(`thinking_json`, `tool_calls_json` stay as-is).

**Effort:** 🔲 ~½ day (share one ordered timeline renderer between streaming and finalize
paths).

---

## P7 · Text-to-speech for AI replies (voice icon under finished messages)

**Goal:** A small **voice icon below finished assistant messages** that speaks the reply,
with provider-agnostic TTS — but **`fish-audio/s2.1-pro-free:free` must work out of the box**.

- **Backend:** new `POST /api/chat/tts` endpoint that synthesizes the message text through
  the configured TTS model (mirrors the existing Whisper STT provider pattern in
  `whisper_stt.py` / `config.py`: `whisper_provider`, `whisper_openrouter_model`). Primary
  path: OpenRouter audio output (`audio: {voice, format}` / `modalities: ["audio"]`) with
  `fish-audio/s2.1-pro-free:free` as the default model — the OpenRouter SDK already handles
  base64 audio chunks. Return an audio file (`audio/mpeg` / `audio/wav`) served via the same
  `/api/files/...` pattern as documents.
- **Provider config:** per-user TTS model + voice selection in the UI; keep the list of
  compatible models open (any provider speaking OpenRouter's audio format), with
  fish-audio as the guaranteed-default.
- **Frontend:** voice icon under finished assistant messages → fetches the audio and shows an
  inline `<audio>` player; cache per (message_id, voice) so replaying doesn't re-synthesize.

**Safety:** per-user rate limiting (reuse `TokenUsageLog`-style accounting — TTS burns the
user's token budget), model allowlist so arbitrary `audio:` model ids can't be requested, and
timeouts on synthesis.

**Effort:** 🔲 ~2 days (TTS endpoint + provider wiring + player UI + rate limit).

---

## P8 · YouTube link previews (real video cards, not bare links)

**Goal:** When the AI sends a **YouTube link**, render a real preview — thumbnail, title,
duration — like YouTube itself, instead of a plain text link.

- **Frontend (`MarkdownRenderer.tsx`):** detect `youtube.com/watch?v=` / `youtu.be/`
  links in assistant messages and render a preview card. Fetch metadata via oEmbed
  (`https://www.youtube.com/oembed?url=...&format=json`) or use the stable thumbnail URL
  `https://i.ytimg.com/vi/<id>/hqdefault.jpg`; clicking the card opens/embeds the player.
- **Embed vs link:** show the preview card inline; a play button swaps in the iframe embed
  (the app already sets `Cross-Origin-Embedder-Policy: credentialless` in `main.py`, so
  YouTube embeds work without breaking the sandbox headers).
- **Bounded scope:** only `youtube.com` and `youtu.be` get previews — other URLs stay plain
  links (no SSRF/parser surface beyond a single hard-coded host pair).

**Safety:** client-side oEmbed fetch with a short timeout and no server round-trip;
link-only fallback when metadata fails.

**Effort:** 🔲 ~1 day (link detection + card UI + embed swap).

---

## P9 · Fine-grained subscription locking (EVERYTHING configurable)

**Goal:** Lock **features, tools, and capabilities behind subscriptions** with fine-grained,
per-plan, fully-configurable entitlements — not just token/image limits.

- **Data model:** extend `subscription_plans` with an `entitlements` JSON column (or a new
  `plan_entitlements` table, same migration pattern as `plan_model_limits`): booleans/limits
  per feature — web search, document editor, render/svg/video, sandbox `run_command`, agentic
  git access (P4), image generation, file uploads, voice input (STT), TTS (P7), YouTube
  previews (P8), conversation branching, memory, theme editing, plus per-model access and the
  existing token/image caps.
- **Enforcement:** a single entitlement-check helper consulted by (a) the tool registry —
  the model simply doesn't see disallowed tools (`skill_registry.get_tool_definitions()` in
  `main.py:1487`), (b) API endpoints (`upload`, `image`, `tts`, `/api/git/*`), and (c) the
  UI (hide/disable locked features). Free plan gets a sane default subset; every flag is
  editable per plan in the Admin Panel → Subscriptions tab.
- **Defaults:** new features default to "included in paid plans, off for Free" — nothing
  silently exposed.

**Safety:** enforcement is server-side first (the UI hiding is cosmetic), the entitlements
check runs against the user's *active* subscription (`user_subscriptions` status/expiry, same
lookup as `chat_stream`), and the owner can always see/override every flag.

**Effort:** 🔲 ~1 week (entitlements schema + registry/endpoint checks + per-plan admin UI +
Free-plan defaults).

---

## P10 · Proton full integration (access tokens)

**Goal:** The AI can **securely read (and, with explicit consent, use) the user's Proton Pass
vaults** — logins, API keys, notes, identity/cards — via [Proton Pass access
tokens](https://proton.me/support/pass-access-tokens). Tokens are scoped by the user to
specific vaults (1h–1yr expiry), logged per-access with a reason by Proton itself, and revoked
instantly by deleting the token — so the AI gets exactly the credentials it was granted,
never the user's Proton password.

- **Access token:** created at `pass.proton.me → Settings → Access tokens → New token` (name,
  expiry, vault selection, "Use for AI agent" toggle → Proton hands back markdown instructions
  for the agent). Requires Pass Plus / Proton Unlimited / Pass Professional / Workspace
  Standard. The token is shown **once** — store it like a provider API key.
- **CLI in the sandbox:** the [Pass CLI](https://protonpass.github.io/pass-cli) runs inside
  the existing per-conversation sandbox (`backend/app/sandbox.py`); its `item list` /
  `item get` / `item edit` commands talk to the user's vaults with the token. Auth
  (`PROTON_PASS_TOKEN` or a per-user token row) is provisioned into the container at first
  use, exactly like P4's per-repo credentials — never embedded in chat.
- **Skills:** new `Skill` subclasses in `backend/app/skills/builtins/` —
  `proton_pass_list` / `proton_pass_get` / `proton_pass_edit`, each passing a **reason**
  string ("Log into my bank's API to reconcile subscriptions") that the CLI forwards to
  Proton's agent-activity log — the user can see *why* every credential was accessed.
- **`proton_pass_get` discipline:** the model must never echo credentials into the chat —
  secrets are redacted in tool output (reuse P4's `_redact` / `all_credentials` scrub-list so
  `run_command` output can't leak them either); the skill returns *what* was found
  (site/username/notes), and the credential itself only goes to tools that need it (e.g. a
  `web_scrape` with the token, or into the sandbox for an API call).
- **Read-only by default:** list/get only. `proton_pass_edit` (create/update/delete items,
  move between vaults) is a per-token opt-in and requires a per-action confirm in the chat UI,
  same policy as P4's read-only repos and the existing `edit_document` discipline.
- **Per-user opt-in:** each user links their own token (Settings → Proton), never a global
  admin credential; the token's vault scope stays in Proton's control.

**Safety:** token stored server-side (hashed/encrypted like API keys), vault scope + expiry
enforced by Proton, redaction of every secret in chat/tool output, read-only default with
opt-in writes + per-action confirmation, per-access reasons surfaced to the user in an audit
view (Proton-side + a local `proton_access_log` mirror), and instant revocation by deleting
the token. P9's entitlement gate covers *who* may link a token (paid-plan feature).

**Effort:** 🔲 ~1 week (token storage + CLI provisioning + 3 skills + redaction + consent UI +
audit view).

---

## Status notes

- **P4 shipped as:** `git_repos` / `git_action_log` / `git_credential_history` tables (migrated
  at boot), `backend/app/git_tools.py` (engine: clone/status/diff/commit/push/PR in the
  per-conversation sandbox worktree, credential redaction, author enforcement, remote
  verification, force-push refusal, branch-name validation), six builtin skills registered via
  the existing skill system, admin allowlist CRUD + audit API under `/api/git` (owner/admin),
  and an Admin Panel → Git tab. Commit author is configurable via `GIT_BOT_NAME` /
  `GIT_BOT_EMAIL` (Admin Panel → API Keys). PRs are created through the GitHub / GitLab /
  Gitea-Forgejo (incl. Codeberg) APIs when a bot token is configured, otherwise the model
  returns a manual compare URL.
- Credential handling: tokens/deploy keys are stored server-side, never returned by the API;
  auth flows through `http.extraHeader` + a path-scoped `credential.useHttpPath` store file
  (remote URLs stay clean); every git-tool output AND `run_command` output is scrubbed against
  current + historical credentials, so secrets cannot reach the chat; rotated/cleared/revoked
  credentials are re-provisioned or purged in live sandboxes.
- Remaining follow-ups (not blockers): SSH host-key pinning instead of `accept-new`,
  approval-gated destructive ops, self-hosted-GitLab API detection.

---

## Dependencies & sequencing

- **P1 (Extrovert)** is blocked on confirming the write API; everything else is unblocked.
- **P2 (Obsidian)** and **P4 (Git)** both depend on a generic "external repo/volume" sync
  helper — worth building once, sharing between them.
- **P3 (VPS agent)** is the most invasive; design its protocol first so it can later host
  long-running agents.
- **P5 (auto-scroll)** and **P6 (tool-timeline order)** are frontend-only quick wins —
  natural first batch, and P6's shared timeline renderer is a prerequisite for P7's
  per-message audio button placement.
- **P7 (TTS)** reuses the Whisper provider pattern (`config.py` / `whisper_stt.py`) and the
  `/api/files` serving path; its rate limiting should use the same accounting **P9** defines
  for entitlements, so P9's schema should land first (or alongside).
- **P8 (YouTube previews)** is frontend-only and independent; needs only the shared
  `MarkdownRenderer` link-detection hook.
- **P9 (subscription gating)** is the biggest cross-cutting change — it touches the tool
  registry, every gated endpoint, and the Admin Panel. Design the entitlements schema before
  P7/P8 so their new features are gated from day one.
- **P10 (Proton Pass)** reuses P4's credential storage/redaction pattern and the sandbox CLI
  provisioning — build it after P4 (which is done) and alongside P9 (token linking should be a
  paid entitlement). The Pass CLI's per-access reason field slots into the same audit
  table design as P3's action log.

No dates attached; this is the backlog, not a commitment.
