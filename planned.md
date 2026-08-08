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

**Effort:** 🟢 implemented.

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

**Effort:** 🟢 implemented.

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

**Effort:** 🟢 implemented.

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

**Effort:** 🟢 implemented.

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

## P11 · Modular skill system (overviewable, marketplace-ready)

**Goal:** Rework the skill system so skills are **self-contained, inspectable modules** with a
manifest, per-user scoping, and a proper overview UI — instead of one flat builtins list
(`backend/app/skills/builtins/`) plus metadata-only DB rows.

- **Skill manifest:** each skill carries `name`, `description`, `input_schema`, `version`,
  `author`, `source` (builtin / user / marketplace), `category`, and **permission scopes**
  (which APIs/credentials it may touch — `git`, `web`, `files`, `sandbox`, `proton`, …).
  `SkillMetadata` in `backend/app/skills/base.py` grows to carry this.
- **Per-user scoping:** a `user_skills` table (user_id, skill_name, enabled, custom config) so
  each user can enable/disable/configure skills for their own chats — the model only sees
  their enabled set (`skill_registry.get_tool_definitions()` in `main.py:1487` filtered per
  user). Reuses the same migration pattern as `plan_model_limits`.
- **Registry + loader refactor:** `SkillRegistry` becomes the single overviewable source —
  listable by category, source, and permission scope; `loader.py` auto-discovers skills from
  `builtins/` and installed marketplace dirs instead of a hand-maintained list.
- **Overview UI:** an "Agent → Skills" view (list, manifest details, enable/disable, config
  forms, permission badges). Replaces the current admin-only `/api/skills` CRUD for
  DB-metadata-only skills with real per-user skill management.
- **Isolation & permissions:** skill execution declares the scopes it needs; the runtime
  enforces them (a skill that declares no `git` scope cannot reach git credentials, no
  `sandbox` scope cannot run `run_command`). Same discipline as P4's per-repo credentials.
- **Agent tab:** Documents, Memory, Custom CSS, Git and Skills live under one "Agent" tab in
  the UI (each user manages their own agentic surface). The Documents/Memory/Custom CSS/Git
  consolidation is already implemented — Skills lands with P11.

**Safety:** permission scopes enforced server-side at execution, per-user skill allowlist,
skills can only consume credentials they declared, and marketplace skills are sandboxed by
scope (never implicit full access).

**Effort:** 🟢 implemented.

---

## P12 · Skill marketplace

**Goal:** A **browseable, installable skill marketplace** — community skills plug into LLMDash
without a code change, with review and sandboxing built in.

- **Catalog:** a `skills.marketplace` source — either a bundled curated list or a remote
  catalog (e.g. a git repo of manifests, mirroring how the sandbox clones repos in P4).
  Each entry: manifest (name/desc/schema/version/author), install URL (git repo or archive),
  category, permissions it requests, rating/installs.
- **Install flow:** Admin (or user, per their permission) installs a skill → cloned/imported
  into a managed skills dir (`backend/app/skills/marketplace/`), manifest validated, version
  recorded. Same provenance approach as P4's per-repo allowlist — no arbitrary code at
  runtime beyond what the manifest declares.
- **Per-user enablement:** installed marketplace skills appear in the Agent → Skills tab;
  each user toggles them on for their chats; P9's entitlements gate *who* may install/use
  paid or privileged skills.
- **Safety:** manifest validation (schema + declared scopes only), review gate (owner approves
  before a skill is enabled for everyone), sandboxed execution via declared permission
  scopes (P11), credentials never implicitly shared, uninstall removes the code + revokes
  access instantly.

**Effort:** 🟢 implemented.

---

## Status notes

- **P7 shipped as:** `POST /api/chat/tts` synthesizes a reply through OpenRouter's audio
  output modality (`modalities: ["audio"]`, `audio: {voice, format}`), default model
  `fish-audio/s2.1-pro-free:free` — works out of the box with just `OPENROUTER_API_KEY`
  (config: `TTS_PROVIDER` / `TTS_OPENROUTER_MODEL` / `TTS_VOICE`, same pattern as Whisper
  STT in `tts.py`). The model id is server-side only (no client-supplied models); an
  optional client `voice` is validated. Returns raw `audio/mpeg` bytes; the response parser
  handles both `message.audio.data` and content-part audio shapes. TTS burns the user's
  token budget: an estimated cost (chars/4, min 25) is checked against the effective
  limit (user/plan `token_limit`) before synthesis and charged to `User.token_usage` +
  `TokenUsageLog` after success; gated by the `tts` entitlement. Frontend: a voice icon
  under finished assistant messages plays the reply via an inline `Audio` element, with a
  per-session per-message cache (no re-synthesis on replay), in-flight guard, cache cap,
  and single-playing-audio semantics; icon hidden when the plan locks TTS.
- **P8 shipped as:** YouTube link previews in `MarkdownRenderer` — `youtube.com/watch?v=`,
  `youtu.be/`, `/shorts/` and `/embed/` links (ids validated to the exact 11-char format)
  render a preview card: client-side oEmbed fetch (6s AbortController timeout, hard-coded
  host, no server round-trip) for title/author/thumbnail with the stable
  `i.ytimg.com/vi/<id>/hqdefault.jpg` fallback, and a play button that swaps in the
  `youtube.com/embed/<id>` iframe (works with the app's `COEP: credentialless` header).
  Bare YouTube URLs in prose are auto-linked via a code-block-aware preprocessor so cards
  appear even without markdown link syntax; non-YouTube URLs are untouched and link-only
  fallback applies when metadata fails. Gated by the `youtube_previews` entitlement
  (passed as a prop from the user's entitlements).
- **P12 shipped as:** browseable + installable skill marketplace. `GET /api/marketplace`
  returns the bundled catalog (`backend/app/marketplace_catalog.json`, empty by default,
  owner-editable) plus installed skills; `POST /api/marketplace/install {url}` clones a git
  repo (https/file/ssh), validates `manifest.json` (name/version/author/category/scopes ⊆
  builtin scopes/entitlement ∈ known keys/flat entry module) and the entry module's Skill
  subclass (name/version/scopes/entitlement must match the manifest), copies it into the
  managed `skills/marketplace/` package where the P11 loader auto-discovers it, and forces
  `source="marketplace"` so a repo can never self-grant. Review gate: installed skills are
  `approved=False` and invisible to regular users (hidden from `/api/skills`,
  `/api/tools` and the chat tool set/execution guard) until an owner/admin approves them
  (`POST /{name}/approve`). `DELETE /{name}` uninstalls: removes the code, unregisters, and
  purges per-user `user_skills` rows instantly. Scope sandboxing is P11's
  `allowed_scopes` guard (chat passes the builtin scope union). Install/approve/uninstall
  are owner/admin-only AND gated by the `skill_marketplace` entitlement. UI: Admin Panel →
  Marketplace tab (catalog, install-from-URL, approve/uninstall); installed skills appear
  in Agent → Skills for per-user enablement once approved. Dockerfile now installs `git`.
  Trust model: installing runs the skill's code in the server process, so the owner is the
  review gate; installed code lives in `skills/marketplace/` (ephemeral in containers).
- **P11 shipped as:** skills are now self-contained manifest modules — every `Skill` carries
  `version` / `author` / `source` (builtin|user|marketplace) / `category` / `scopes`
  (declared permission scopes: git, web, files, sandbox, theme, render, …) plus the P9
  `entitlement` key. The loader auto-discovers skills by scanning `skills/builtins/` and the
  (P12-populated) `skills/marketplace/` package instead of a hand-maintained list. A
  `user_skills` table (user_id, skill_name, enabled, config_json) gives every user
  per-skill enable/disable + custom config; the model only sees the user's enabled set
  (filtered together with P9 entitlements in `get_tool_definitions`, with a
  defense-in-depth check at execution). Scope enforcement lives in the registry
  (`execute(allowed_scopes=...)`) — ready for P12 marketplace installs. The old admin CRUD
  for metadata-only DB skills is removed; `GET /api/skills` now returns every skill's
  manifest + per-user state, with `PUT /api/skills/{name}/enable` and
  `/config` per-user endpoints. UI: Agent → Skills tab (list grouped by source, manifest
  details, permission badges, enable toggle, JSON config editor), gated by the
  `skills_management` entitlement.
- **P9 shipped as:** per-plan `entitlements` JSON on `subscription_plans` (19 feature keys —
  all existing features plus every planned one (P1/P2/P3/P7/P8/P10/P11/P12), enforced as each
  feature ships) and a per-model access gate `plan_model_limits.allowed`. Defaults are
  **all-on for every plan, including Free** — Free is the implicit fallback plan for users
  without a subscription (including the owner of a self-hosted instance), so locking
  features by default would break the primary use case; nothing is locked until an admin
  turns a flag off in Admin Panel → Subscriptions. Enforcement is server-side first: the
  tool registry hides disallowed skills from the model (each skill carries an `entitlement`
  tag, with a defense-in-depth check at execution time), endpoints 403 via
  `require_entitlement` (upload, transcribe, image, branch, documents, git, memory, theme/css
  writes), `chat_stream` skips memory injection/capture when `memory` is off and rejects
  denied models, non-admin model lists hide denied models, and `/api/auth/me` carries the
  merged entitlements so the UI hides locked features (upload/voice buttons, Agent tabs).
- **P5 shipped as:** smart auto-scroll — the viewport only follows the newest content while
  the user is already at the bottom (100px threshold); if the user has scrolled up, the
  viewport is left alone mid-generation. A sticky "Latest" button appears whenever the user
  is not at the bottom while a generation is active or has just finished, and jumps back
  down on click. The behavior is controlled by a per-user `auto-scroll` setting
  (`users.auto_scroll`, default on) toggled in Agent → Appearance and persisted via
  `/api/auth/auto-scroll` (same pattern as theme/css).
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
- **P11 (modular skills)** is the foundation for P12 — the manifest/scoping refactor must land
  before the marketplace can validate and sandbox community skills. Both sit naturally with
  the "Agent" tab consolidation (Documents/Memory/Custom CSS/Git/Skills under one user-facing
  surface).
- **P12 (marketplace)** depends on P11 and on P9's entitlements to gate installs/usage of
  privileged skills.

No dates attached; this is the backlog, not a commitment.
