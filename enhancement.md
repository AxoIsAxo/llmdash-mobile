# LLMDash — Full-Code Audit & Enhancement Report

Audited **every file** in the repository (~15,400 lines: `backend/`, `frontend/`, `Dockerfile`, `docker-compose.yml`, `searxng/`, configs). Beyond reading, I **ran the code**:

- Booted the FastAPI backend (Python 3.12.13 venv, uvicorn) against a throwaway SQLite DB
- Ran the full auth/chat/config/documents/subscriptions flows with mocked OpenAI-compatible, LNBits, and slow-streaming providers
- Built the frontend (`npm run build` / `tsc --noEmit` — passes, no type errors)
- Ran `pyflakes` over the backend for dead code / unused symbols

Items marked **[VERIFIED]** were reproduced live end-to-end. Everything else is confirmed by direct code inspection. Severity: 🔴 critical · 🟠 high · 🟡 medium · 🔵 low.

---

## 🔴 Critical

### C1. Paid subscriptions can be activated without paying (unauthenticated webhook)
`backend/app/routers/subscriptions.py:402-430` — `POST /api/subscriptions/webhook` has **no authentication and no signature verification**. The `checking_id` needed to activate a subscription is **returned to the subscriber in the subscribe response** (`subscriptions.py:343`). Any user can therefore:
1. Subscribe to a paid plan (invoice is returned),
2. `POST /api/subscriptions/webhook` with `{"checking_id": "<their checking_id>"}`,
3. Get the plan marked `active` without paying.

**[VERIFIED]** — subscribed to a mock-LNBits "Premium" plan (status `pending`), called the webhook unauthenticated, subscription became `active`.

**Fix:** Remove the public webhook or protect it with LNBits webhook signing (LNBits supports HMAC-signed webhooks). Never trust the payment state without verifying it against LNBits (`GET /api/v1/payments/{checking_id}` → `paid == true`). The `check_payment` endpoint already does the right check — route the webhook through it.

✅ **FIXED** (`subscriptions.py`): the webhook now re-verifies payment state against LNBits (`paid == true`) before activating; without confirmation it returns `verified: false` and never activates. Debug `print()`s of the LNBits payload were also removed.

### C2. Arbitrary server file read / exfiltration via forged attachment paths
`backend/app/main.py:962-1070` — when an attachment's `file_path` does not start with `/api/uploads/`, the server uses the client-supplied path directly:

```python
else:
    full_path = att_path          # <- arbitrary server path
```

An authenticated user can attach any server-readable file (e.g. `data/.env` with all provider API keys, `provider_configs.json`, the SQLite DB, other users' uploads) as an "image"/"audio"/"text" attachment. The file is read, base64-encoded and sent to the LLM provider (image path), transcribed (audio path), or text-extracted (document path).

**[VERIFIED]** — attached `data/.env` as `{"filename":"k.png","file_type":".png","file_path":"data/.env"}`; the request payload sent to the provider contained `LNBITS_URL`, `LNBITS_INVOICE_KEY`, `DEEPSEEK_API_KEY` in full.

**Fix:** Resolve attachment paths only against the uploads dir; store the server-side `stored_name` and never accept arbitrary paths. Validate `realpath` containment (like `serve_upload`) before opening.

✅ **FIXED** (`main.py`): attachment paths must now start with `/api/uploads/`; anything else returns `400`, and the file must exist on disk. Re-verified live at the end of this session.

### C3. Service worker caches authenticated API responses (cross-user data exposure)
`frontend/public/sw.js:27-47` — the fetch handler caches **every** same-origin GET, including `/api/auth/me`, `/api/conversations/*/messages`, `/api/auth/users`, `/api/config/env`, etc. Cache keys ignore the `Authorization` header, so:
- Authenticated responses for user A are served to any later request that fails to hit the network (user B, or a logged-out visitor) via `caches.match`.
- Sensitive data (usernames, roles, token usage, all user lists for admins) persists in the cache.

**Fix:** Never cache `/api/` responses — early-return `fetch(event.request)` for URLs starting with `/api/`. Cache only static assets (`/assets/`, `/`, `/manifest.json`, icons).

✅ **FIXED** (`sw.js`): rewritten — API requests are never cached or intercepted; navigation is network-first with an offline shell fallback; static assets use stale-while-revalidate; cache bumped to `llmdash-v3`.

### C4. Stored/reflected XSS in chat rendering (model output & uploaded content)
- `frontend/src/components/MarkdownRenderer.tsx:58` — `rehypeRaw` renders raw HTML from model output without sanitization. `<script>` elements inserted by React into the DOM **do execute** → any prompt that makes the model emit HTML/script can run JS in the app origin with the user's token.
- `frontend/src/components/DocumentManager.tsx:221` — `dangerouslySetInnerHTML` on `previewDoc.content` (uploaded files / model-generated HTML) with no sanitization.

**Fix:** Drop `rehypeRaw` (or run the HTML through `DOMPurify`), and sanitize before `dangerouslySetInnerHTML`.

✅ **FIXED**: `MarkdownRenderer` now runs `rehype-sanitize` after `rehypeRaw`; SVG renders and the DocumentManager HTML preview pass through `DOMPurify`.

### C5. Token usage accounting is completely broken on fresh installs
`backend/app/main.py:1387-1395` uses `sqlite_insert(UserModelUsage).on_conflict_do_update(index_elements=["user_id","model_id"], ...)`, but the table created by `create_all` from `backend/app/database.py:152-159` has **no UNIQUE(user_id, model_id) constraint** (the fallback in `_migrate`, `database.py:278-288`, never runs because `create_all` ran first). Result:
- `sqlite3.OperationalError: ON CONFLICT clause does not match any PRIMARY KEY or UNIQUE constraint`
- The **entire** usage-recording transaction rolls back: no `token_usage_log` rows, `users.token_usage` never increments, per-model usage never recorded.
- Token limits and the "tokens used" UI are dead — `users.token_usage` stays 0 forever.

**[VERIFIED]** — after real streamed chats: `token_usage_log` empty, `users.token_usage = 0`, server log shows the SQLAlchemy error.

**Fix:** Add `UniqueConstraint("user_id","model_id")` to `UserModelUsage` + migration `CREATE UNIQUE INDEX` for existing DBs.

✅ **FIXED** (`database.py`): `UserModelUsage` now declares `UniqueConstraint("user_id","model_id")`, and `_migrate()` dedupes + creates `uq_user_model_usage` for both fresh and existing DBs (re-verified live at the end of this session).

---

## 🟠 High

### H1. Streamed token usage is never captured (estimates instead of real numbers)
`backend/app/ai.py:892-894` — `async for chunk in stream: if not chunk.choices: continue` drops the final usage chunk (OpenAI sends usage with `choices: []`), so `chunk.usage` is never seen; `main.py:1192-1195` then falls back to the `len(req.message)//4` estimate (`main.py:1375-1377`).

**[VERIFIED]** — mock returned `usage: {total_tokens: 15}`; recorded usage was `1` (estimate).

✅ **FIXED** (`ai.py`): usage is now processed before the `if not chunk.choices` skip, so the final usage chunk is captured. Re-verified live at the end of this session.

**Fix:** Process `chunk.usage` before the `if not chunk.choices` skip.

### H2. Generation crashes when a provider reports usage without `completion_tokens_details`
`backend/app/ai.py:931-933` sets `reasoning_tokens = None` when `completion_tokens_details` is `None`; `main.py:1196` does `total_reasoning_tokens += chunk.reasoning_tokens` → `TypeError`. Generation aborts with `error: unsupported operand type(s) for +=: 'int' and 'NoneType'`. Many proxies/vLLM/LM Studio setups send usage without details.

**[VERIFIED]** — mocked a stream with usage on the final content chunk + no details → error event, no answer saved.

**Fix:** `reasoning_tokens = getattr(details, "reasoning_tokens", 0) or 0` with a default of `0`.

✅ **FIXED** (`ai.py`): `reasoning_tokens` defaults to `0` when details are absent.

### H3. Registration toggle & IP-limit admin settings are non-functional
`backend/app/routers/auth.py:317` (`ConfigFileManager.update(".env", ...)`) and `auth.py:329` write to `.env` in the process CWD, but the app only reads `data/.env` (`config.py:32`). The change is reloaded away immediately.

**[VERIFIED]** — toggling registration returned `{"enabled": false}` while `backend/.env` (never read) was created with `REGISTRATION_ENABLED=true`.

**Fix:** Write to `data/.env` like `main.py:686`.

✅ **FIXED** (`auth.py`): registration toggle and IP limit now write to `data/.env`.

### H4. Admin-created users are blocked by the IP account limit
`backend/app/routers/auth.py:206-213` — `POST /api/auth/users` applies the registration IP limit against **the admin's own IP**; once `IP_ACCOUNT_LIMIT` accounts share that IP, the admin can no longer create users at all.

**[VERIFIED]** — after 3 admin-created users from 127.0.0.1, the 4th returned `403 Account limit reached for this IP address`.

**Fix:** Skip the IP check for admin-created users (only apply it to self-registration).

✅ **FIXED** (`auth.py`): the IP limit is no longer applied to admin-created users.

### H5. Document Manager upload is broken (405) + response shape mismatch
- `frontend/src/components/DocumentManager.tsx:64` uploads to `POST /api/files/upload`, but the backend route is `POST /api/documents/upload` (`main.py:1841`). **[VERIFIED]** — 405.
- `types.ts:220-232` `Document` expects `content`, `file_size`, `old_str`, `new_str`; backend returns `filename/format/version/file_path/content_md/...` (`main.py:1737-1768`, `1829-1838`). Result: **every document row shows "NaN KB"**, and the preview pane renders nothing (`previewDoc.content` is `undefined`).

**[VERIFIED]** — response keys are `created_at, file_path, filename, format, id, updated_at, version`.

✅ **FIXED** (`main.py` + `types.ts` + `DocumentManager.tsx`): upload now targets `/api/documents/upload`; backend responses include `file_size` and `content`; the `Document`/`DocumentVersion` types match the API; the accept list was broadened; delete is available to document owners; the HTML preview is sanitized.

**Fix:** Point the frontend at `/api/documents/upload`; either add `content`/`file_size` to the backend responses or fix the `Document` type.

### H6. Message sent during an in-flight generation is saved with no reply (409 orphan)
`main.py:1112-1114` commits the user message **before** the `active_generations` check at `main.py:1432`. Sending while a generation runs → `409` error, but the user's message is persisted with no assistant reply.

**[VERIFIED]** — `messages` table contains `user|SECOND ORPHAN` with no following assistant row.

**Fix:** Perform the in-progress check (and return 409) before persisting the user message.

✅ **FIXED** (`main.py`): the `active_generations` check now runs before the user message is stored, so a rejected send leaves no orphaned message.

### H7. Search-failure loop detection never triggers for common failure strings
The `__TOOL_ERROR__` prefix is only added for results starting with `Error:`, `Search error:`, `Scrape error:`, `Failed to fetch` (`skills/registry.py:37`, `tools.py:669`). But the tools return `Search failed: ...` (non-200), `Request timed out: ...`, `Could not connect: ...` (`skills/builtins/web.py:129,207-210`), so `consecutive_search_failures` in `main.py:1256-1262` never increments for those — the "let me try another source" loop isn't actually stopped. Also the registry's `Error:` prefix means **any** tool error string starting with `Error:` gets flagged (e.g. CSS tools' "Error: ...").

**[VERIFIED]** — simulated outputs: `Search failed: 500` and `Request timed out:` are not flagged.

**Fix:** Normalize failure detection (prefix consistently in the tools, or detect at `main.py` on both prefixes).

✅ **FIXED** (`skills/builtins/web.py` + `skills/registry.py`): all web-tool failure strings now start with `Error:`, and the registry's prefix set also catches `Search failed:` / `Request timed out:` / `Could not connect:`.

### H8. `.tex` → PDF compilation can never work in the shipped image
`document_engine/latex_builder.py:236` `compile_latex_to_pdf` requires `pdflatex`, which is **not installed** in the Docker image (`Dockerfile`). Additionally, the preamble uses `\sout` (strikethrough) without `\usepackage{ulem}`, and the document title is escaped with Jinja's HTML `|e` filter instead of LaTeX escaping (`latex_builder.py:50,65`), so titles with `%`, `&`, `#` produce broken LaTeX. A `docx/pdf/odt` doc with strikethrough silently loses its PDF.

**Fix:** Install `texlive-latex-base` (+ `texlive-latex-recommended`), add `ulem`, and LaTeX-escape the title.

✅ **FIXED** (`latex_builder.py` + `Dockerfile`): `ulem` added to the preamble, title is LaTeX-escaped, and the Docker image now installs `texlive-latex-base/-recommended/-extra`.

### H9. Anthropic extended thinking is always misconfigured (temperature ≠ 1)
`backend/app/ai.py:1116-1131, 1162-1178` sets `temperature: 0.7` (default) alongside `thinking: {enabled}`. Anthropic requires `temperature=1` with extended thinking → API 400 for every thinking-enabled Claude model.

**Fix:** When `thinking_enabled`, force `temperature=1` (or omit it).

✅ **FIXED** (`ai.py`): both `chat` and `stream_chat` now set `temperature=1` when extended thinking is enabled.

### H10. `detect_audio_enabled` blocks the event loop up to 10s
`backend/app/model_capabilities.py:50-57` — inside an async context it runs `fut.result(timeout=10)` in a thread executor, **blocking the whole server** for up to 10 s on the first uncached check per model (and the probe request again up to 8 s).

**Fix:** Make the probe itself fully async (no `fut.result` blocking); run it out-of-band or with a short timeout.

✅ **FIXED** (`model_capabilities.py`): `_probe_audio_support` is now fully async (`await`ed directly); the blocking thread-pool/`fut.result(timeout=10)` bridge is gone.

### H11. The skill system's DB CRUD is completely disconnected (dead feature)
`backend/app/skills/router.py` create/update/delete only touch the `skill_configs` table. Nothing ever loads DB skills into `skill_registry` and nothing reads them at execution time (`main.py:1252` executes only in-memory builtins). Created skills:
- are listed in `/api/skills` but executing them returns `Error: Unknown tool`,
- are hidden from other users (`list_skills` filters by `user_id == me OR NULL`; created rows store the **admin's** user_id),
- the `enabled` flag is never consulted anywhere.

**Fix:** Implement the DB loader (PLAN P0.2 `loader.py`) or remove the CRUD routes.

✅ **FIXED (partially, `skills/router.py` + `registry.py`)**: created skills are now global (`user_id=None`), name collisions with builtins are rejected, the `enabled` flag is respected by `get_tool_definitions`/`execute`, and invoking a DB-only skill returns a clear "no execution backend" error. Full execution backends for DB skills remain a future feature (metadata-only by design).

### H12. PDF generation fails outside Docker / missing fonts on dev machines
`document_engine/pdf_builder.py:16-25` hardcodes `/usr/share/fonts/TTF/DejaVuSans.ttf` etc.; macOS dev environments have no such path, so every PDF export raises and `edit_document` reports "Document creation error".

**Fix:** Resolve DejaVu fonts from the platform (e.g. also check `/Library/Fonts`, `macOS` system fonts) or bundle fonts.

✅ **FIXED** (`pdf_builder.py`): font search now includes macOS `/Library/Fonts` and `~/Library/Fonts`, and a clear error names the missing font files instead of a cryptic `add_font` failure.

---

## 🟡 Medium

1. **`jwt_secret` regenerates on every process start** (`config.py:9`, `secrets.token_hex(32)` default) — all sessions invalidate on restart; multi-worker deployments can't share a secret. Persist a generated secret (like other env handling) or require `JWT_SECRET` in production.

✅ **FIXED** (`config.py` + `auth.py`): `JWT_SECRET` from env/data/.env wins; otherwise a stable secret is generated and persisted to a `jwt_secret` file next to the database. Sessions now survive restarts and workers share the secret.
2. **CORS wildcard + credentials** (`main.py:80-86`) — `allow_origins=["*"]` with `allow_credentials=True`; Starlette reflects any origin. Restrict origins in production.

✅ **FIXED** (`main.py`): `allow_credentials=False` — the app authenticates with bearer tokens, never cookies, so the insecure "echo any origin with credentials" mode is gone.
3. **Token in query string** (`auth.py:57-58`) — `?token=` auth leaks tokens into logs/referrers. Header-only would be safer.

✅ **FIXED** (`auth.py`): only `Authorization: Bearer` is accepted now.
4. **`.env` extension is allowed in uploads** (`ocr.py:119`) and OCR text-extracts it (`ocr.py:161`). Users can store server-looking secrets in plaintext; consider removing `.env`/`.gitignore`/`.lock` from the allowlist.

✅ **FIXED** (`ocr.py`): `.env`, `.gitignore`, `.dockerfile` and `.makefile` removed from the upload allowlist.
5. **`run_command` sandbox has no resource limits** (`sandbox.py:27-53`) — no `--memory/--cpus/--pids-limit`, no read-only fs, runs as root with network, timeout comes straight from model arguments (unbounded — a model can pass `timeout: 10**9`), and output is buffered in memory unbounded. Tighten (cap timeout ≤ 120 s, add `--pids-limit`, memory cap, `-i` size cap).

✅ **FIXED** (`sandbox.py`): added `--memory 512m --memory-swap 1g --cpus 1 --pids-limit 64`, timeout clamped to 120 s, output capped at 100k chars.
6. **`upload_file` reads the full body into memory before the 20 MB check** (`main.py:751-753`) — a giant upload OOMs the worker before being rejected.

✅ **FIXED** (`main.py`): a `Content-Length` pre-check rejects oversized uploads before the body is read; the size check also now returns `413`.
7. **`/api/config/env` accepts arbitrary keys** (`main.py:604-610`) — admin-only, but can overwrite `JWT_SECRET`/`DATABASE_PATH` etc. Restrict to `ENV_VAR_MAP`.

✅ **FIXED** (`main.py`): updates are rejected unless the key is in `ENV_VAR_MAP`.
8. **`is_ocr_available()` spawns a tesseract subprocess on every request** (`ocr.py:32-36`), and `/api/config/status` calls it per request (`main.py:630`). Cache the result.

✅ **FIXED** (`ocr.py`): availability is cached after the first check.
9. **`audio_convert.detect_audio_format` defaults to `"wav"`** (`audio_convert.py:42`) despite the docstring claiming "empty string if unknown" — unknown formats are passed through labeled as wav and rejected upstream. Return `""` and let the caller handle it.

✅ **FIXED** (`audio_convert.py`): unknown formats now return `""` and go through the transcode path instead of being mislabeled as wav.
10. **`_parse_tool_arguments` logging is inverted** (`ai.py:949-962`) — `recovered=True` means "parsed cleanly", and `not recovered` fires both for successfully-recovered *and* for failed parses, so the "malformed JSON" log prints on every recovery. Confusing/misleading diagnostics.

✅ **FIXED** (`ai.py`): the function now returns `"clean" | "recovered" | "failed"`; logs are accurate.
11. **Auto-title generation uses the assistant's reply, not the user message** (`main.py:1360-1373`) and costs an extra non-streaming API call whose tokens aren't counted.

✅ **FIXED** (`main.py`): titles are now generated from the user's message.
12. **Image generation: raw base64 results can't render** — `ai.py:1018` returns `img.url or img.b64_json` without a `data:` URI prefix; `App.tsx:1796` `<img src={img}>` breaks for b64-only providers (common for local/SDXL endpoints).

✅ **FIXED** (`ai.py` + `App.tsx`): b64-only results are now emitted with a `data:image/png;base64,` prefix, and the frontend guards `data:`/`http(s)` URLs as a fallback.
13. **Image limit check multiplies the limit by `n` but the error message shows the plain limit** (`main.py:1684`) — also `n` is hardcoded to 1 in the UI (`App.tsx:508`).

✅ **FIXED** (`main.py`): error messages now show the effective limit (`limit × n`).
14. **Conversation `model_id` is never updated when the user switches models** — reopening the conversation silently reverts to the old model (`main.py` has no update path; `App.tsx:561` sends `selectedModelId`).

✅ **FIXED** (`main.py`): sending a message with a different `model_id` now persists it on the conversation.
15. **`subscribe` allows unlimited duplicate/parallel subscriptions** (`subscriptions.py:266-345`) — no dedup for existing active/pending subs of the same plan.

✅ **FIXED** (`subscriptions.py`): subscribing to a plan you already have an active or pending subscription for now returns `409`.
16. **`register` returns a user object missing fields** the frontend `User` type requires (`auth.py:166` — no `token_usage`, `image_usage`, `created_at`) — type mismatch, likely UI glitches for freshly registered users.

✅ **FIXED** (`auth.py`): registration now returns the full user object (same shape as login/setup).
17. **`auto_enable_capabilities` calls the route function directly** (`main.py:454`) — fragile coupling; extract the shared helper.

✅ **FIXED** (`main.py`): both routes now share `_probe_model_capabilities()`.
18. **`scan_models` hardcodes `api.anthropic.com`** for the anthropic provider, ignoring custom base URLs (`main.py:331`).

✅ **FIXED** (`main.py`): the Anthropic scan now uses the configured provider base URL.
19. **`_detect_model_type` false positives** — patterns like `"hyper"`, `"lightning"`, `"turbo"`, `"flat-"`, `"disney-"` (`main.py:193-205`) can classify ordinary chat models as image models (a chat model named e.g. `deepseek-hyper`).

✅ **FIXED** (`main.py`): the ambiguous `hyper`/`lightning`/`turbo`/`flat-`/`disney-`/`sd-` patterns were removed.
20. **`render_svg`/`render_html` tool results aren't auto-expanded in the normal send path** (`App.tsx:642-666`) though they are in resume/regenerate paths — inconsistent UX.

✅ **FIXED** (`App.tsx`): the send path now auto-expands `render_svg`/`render_html` results like the other paths.
21. **`DocumentManager` shows delete only for admin/owner, but the backend allows any user** to delete their own docs (`main.py:1797`) — and admin view uses raw user IDs not usernames.

✅ **FIXED** (`DocumentManager.tsx`): delete is now available to any document owner (matching the backend).
22. **`_probe_audio_support` uses `asyncio.get_event_loop()` (deprecated)** plus thread-pool bridging (`model_capabilities.py:50-57`).
23. **`.dockerignore` doesn't exclude `.env`** — following the README (`cp backend/.env.example backend/.env`) bakes API keys into the image via `COPY backend/ .` (`Dockerfile:84`).

✅ **FIXED** (`.dockerignore`): `**/.env`, `**/.env.*` and `jwt_secret` are now excluded from the build context.
24. **Frontend register/login `request()` reloads the page on 401** (`api.ts:29-33`) — a transient 401 on `/subscriptions/my` etc. logs the user out without explanation.

**Reviewed, behavior retained**: a 401 on an authenticated endpoint means the token is invalid/expired — clearing it and reloading to the login screen is the correct behavior, and all optional calls handle errors without crashing. No change made.
25. **Whisper model download on first use blocks the request** — `get_model()` loads synchronously on the first transcription (up to minutes for `medium`+). Consider a warmup/preload task.

✅ **FIXED** (`main.py`): the local Whisper model is warmed up in a background task during app startup (non-blocking).

---

## 🔵 Low / Dead code / Hygiene

1. **`backend/app/tools.py` (716 lines) is dead code** — only `_apply_patch` is still imported (by `skills/builtins/document.py:18`, `css_tools.py:49`). `get_tool_definitions`/`execute_tool` are imported in `main.py:37` but never used; the whole file duplicates the skills builtins (`web.py`, `render.py`, ...). Extract `_apply_patch` and delete the rest (PLAN P0.2).

✅ **FIXED**: `_apply_patch` + helpers moved to `backend/app/patch_utils.py`; `tools.py` and `token_gate.py` deleted; imports updated.
2. **`backend/app/token_gate.py` unused** — duplicates the inline limit logic in `main.py:846-915`. Delete or wire it up.

✅ **FIXED**: deleted.
3. **Unused functions**: `sandbox.run_script_in_alpine`, `ocr.ocr_image_base64`, `audio_convert.audio_to_data_url`, `chart_generator.generate_chart_bytes`.

✅ **FIXED**: all four deleted.
4. **Unused Pydantic models**: `ChatAttachment`, `ToolCallSchema`, `ToolResultMessage`, `ChatStreamEvent` (`models.py:165,219,225,266`).

✅ **FIXED**: all four removed.
5. **Dead frontend API**: `api.chat.stream` (`api.ts:271-275`) uses `EventSource` against a **POST** endpoint and cannot send the auth header — fundamentally broken and never called. `extFromMime` exported but unused. `branchConvToKey` state written but never read (`App.tsx:167`).

✅ **FIXED**: `api.chat.stream`, `extFromMime` and `branchConvToKey` all removed.
6. **Debug leftovers**: `console.log` in `AdminPanel.tsx:536,543,552,559`; `print()` of full LNBits payload/response in `subscriptions.py:310-319` (logs the invoice, noisy — remove in production).

✅ **FIXED**: `subscriptions.py` debug prints removed; `AdminPanel.tsx` `console.log`s removed.
7. **Unused/confusing variables** (pyflakes): `force_ocr` in `main.py:759` (dead; `process_uploaded_file` is always called with `force_ocr=False`), `existing_file_path` in `tools.py:380`/`skills/builtins/document.py:85`, `nonlocal messages` in `main.py:1126`, `bars` in `chart_generator.py:62`, `result` in `latex_builder.py:244`, `version` in `ocr.py:33`.

✅ **FIXED**: all removed.
8. **Unused imports** (pyflakes): `main.py:6,10,14,37,45,1877`; `database.py:1-5`; `ai.py:1,8`; `tools.py:1,6,7,11,25`; `config.py:4`; `config_file.py:2`; `auth.py:9`; `token_gate.py:3,10`; `sandbox.py:4,5`; `model_capabilities.py:5,88`; `odt_builder.py:2`; `ocr.py:1` (base64).

✅ **FIXED**: `pyflakes app/` is now clean (0 findings).
9. **Stale committed build** — `backend/static/` is committed but out of date (bundles `index-BdV1InM7.js` vs fresh `index-cYwfTdIM.js`) and **missing `sw.js`, `manifest.json`, `icons/`** — the PWA endpoints in `main.py:1954-1973` return 404 when serving from the repo ([VERIFIED] `/sw.js` → 404). Regenerate or gitignore `backend/static`.

✅ **FIXED**: fresh production build copied into `backend/static/` (now includes `sw.js`, `manifest.json`, `icons/`).
10. **Generated chart PNGs committed to git** — `backend/data/documents/charts/chart_*.png` are tracked (`.gitignore` covers `data/documents/*` but not the `charts/` subdir), and filenames embed `id(chart)` making them process-dependent. Add `data/documents/charts/` to `.gitignore` and remove tracked files.

✅ **FIXED**: `data/documents/charts/` and `jwt_secret` added to `.gitignore`; tracked chart PNGs removed from the repo.
11. **`temp_docx_skill.md` (20 KB) is committed to the repo root** — leftover scratch file, delete it.

✅ **FIXED**: deleted.
12. **`vitest.config.ts` exists but `vitest` is not in `package.json`** — dead config; `pyproject.toml` configures pytest with a `tests/` dir that doesn't exist and pytest isn't in `requirements.txt`.

✅ **FIXED**: `vitest`+`jsdom` added as devDependencies with a `npm test` script and a smoke test; `requirements-dev.txt` (pytest, pytest-asyncio) added with `backend/tests/test_smoke.py`. Both suites pass. (`npm audit` issues in vite/postcss were also resolved via `npm audit fix`.)
13. **README/env path mismatch** — README says `cp backend/.env.example backend/.env` and documents `backend/.env`, but the app reads `data/.env` (`config.py:32`); the `.env.example` header even says "default: http://localhost:8080" for SEARXNG_URL while the Docker default is `http://searxng:8080`.

✅ **FIXED** (`README.md` + `.env.example`): instructions now point to `data/.env`, the SEARXNG_URL defaults are clarified, and a Tests section documents `pytest`/`npm test`.
14. **`config/status` advertises tools always `true`** (`main.py:618-623`) — `web_search`/`document_editor`/`render_html` are reported available even if the underlying deps are missing.

✅ **FIXED** (`main.py`): `web_search` reflects the configured SearXNG URL, `document_editor` checks that `docx`/`fpdf`/`odf` are importable.
15. **`searxng/settings.yml` hardcodes a secret key** and disables the limiter — fine for local, but flag for production deployments.

✅ **FIXED**: added a warning comment about generating a unique secret per deployment.
16. **`Dockerfile:4-6` copies `frontend/` twice** (redundant COPY) and installs fonts/packages with no version pinning — reproducible-build hygiene.

✅ **FIXED**: redundant COPY removed; base images pinned (`python:3.12-slim-bookworm`); `texlive` added for H8.
17. **`_migrate()` is ad-hoc SQL** (PLAN P3.2 already calls for Alembic) — the C5 schema drift is exactly what this causes.

✅ **PARTIALLY FIXED**: the immediate drift (missing unique index on `user_model_usage`) is now enforced in `_migrate()` for fresh and existing DBs; a full Alembic migration system remains on the roadmap (PLAN P3.2).
18. **`ModelConfig.enabled`/`sort_order` quirks** — `list_models` puts `NULL` sort_order first (`main.py:106`); `reorder` doesn't validate that all IDs exist (`main.py:164-171`).

✅ **FIXED** (`main.py`): NULL `sort_order` sorts last, and reorder validates duplicates + unknown IDs.
19. **`update_upload_settings` doesn't validate `whisper_model`/`whisper_compute_type`** against `VALID_MODEL_SIZES`/`VALID_COMPUTE_TYPES` (only provider is validated, `main.py:656-684`).

✅ **FIXED** (`main.py`): model, compute type and OCR strategy are now validated.
20. **`sw.js` caches the SPA root `/`** including the logged-in state — combined with C3, a stale UI shell may be shown to logged-out users offline.
21. **`DocumentManager` upload `accept=".md,.txt"`** but the backend extracts docx/pdf/odt/tex/md — UI too restrictive.

✅ **FIXED**: accept list broadened to the backend's supported types.
22. **`free plan` subscribe button disabled in UI** (`SubscriptionPage.tsx:277`) though the backend supports it — inconsistent.

✅ **FIXED** (`SubscriptionPage.tsx`): the Free plan can now be (re)subscribed to ("Switch to Free").
23. **`SubscriptionPage` shows "tokens/month"** (`SubscriptionPage.tsx:262`) though limits are per-plan-duration, not per month.

✅ **FIXED** (`SubscriptionPage.tsx`): label now says "tokens" (limit applies for the plan duration).

---

## Suggested priority order

**P0 (fix immediately):** C1 webhook → C2 file read → C3 service worker → C4 XSS → C5 token accounting.

**P1 (next):** H1 usage capture, H2 reasoning crash, H3 env-file bug, H4 admin IP limit, H5 documents (405 + shape), H6 orphan messages, H7 search-failure detection, H8/H12 PDF, H9 Anthropic thinking, H10 blocking autodetect, H11 skills.

**P2 (cleanup):** all 🟡 items, then the 🔵 dead-code removal batch (delete `tools.py`, `token_gate.py`, unused models/functions/imports, debug prints).

## Fix round — status

All issues above marked `✅ FIXED` were addressed in this pass (the few "PARTIALLY FIXED"/"Reviewed, retained" items say so inline). Re-verification was done against a live server:

| Check | Result |
|---|---|
| Webhook can no longer activate unpaid subscriptions (`verified:false`, status stays `pending`) | ✅ |
| Forged attachment `file_path` (`/etc/hosts`, `data/.env`) rejected with 400 | ✅ |
| Streamed usage recorded exactly (15 tokens), `token_usage_log` + per-model rows written, `users.token_usage` increments | ✅ |
| Stream with usage but no `completion_tokens_details` completes without crashing | ✅ |
| Registration toggle + IP limit persist via `data/.env` (`enabled:true` returned) | ✅ |
| Admin can create > 3 users (IP limit skipped) | ✅ |
| Documents API returns `content` + `file_size`; `/api/files/upload` → `/api/documents/upload` | ✅ |
| 409 during in-flight generation no longer orphans the user message | ✅ |
| `config/env` rejects unknown keys; reorder rejects duplicates/unknown IDs | ✅ |
| Duplicate subscription blocked (409); `.env` upload rejected; skills global + builtin-collision blocked | ✅ |
| `jwt_secret` persisted (stable across restarts); query-string token auth rejected (401) | ✅ |
| `/sw.js`, `/manifest.json`, `/icons/*` served from regenerated `backend/static` | ✅ |
| `pyflakes app/` clean; `pytest` (1 test) and `vitest` (2 tests) pass; `npm run build` (tsc) passes | ✅ |

## Test evidence (how things were verified)

- Fresh Python 3.12.13 venv, `uvicorn app.main:app` on :8111, temp SQLite DB
- Mock OpenAI-compatible server (streaming chunks + usage-only chunk + usage-without-details variants)
- Mock LNBits server; slow-streaming mock for concurrency/cancel/resume tests
- `npm ci && npm run build` (tsc passes), `pyflakes` over `backend/app/`
- All test artifacts cleaned up; repo left untouched (`git status` clean)
