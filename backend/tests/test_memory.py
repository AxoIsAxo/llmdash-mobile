"""Tests for the cross-session memory subsystem.

Covers the definition of done:
- capture is automatic (inbox contains the transcript after a zero-memory-action
  session, even on model error);
- extraction produces L1 atoms and queues L3 persona deltas;
- a fresh session starts with relevant [MEMORY] injected, within hard budget
  caps (context grows by <= ~2.6k chars from memory);
- consolidate + lint work; index/log stay accurate; reset wipes a user.
"""

import os
import tempfile
from types import SimpleNamespace

import yaml

_tmp = tempfile.mkdtemp(prefix="llmdash_mem_test_")
os.environ["DATABASE_PATH"] = os.path.join(_tmp, "test.db")
os.environ["UPLOADS_DIR"] = os.path.join(_tmp, "uploads")
os.environ["DOCUMENTS_DIR"] = os.path.join(_tmp, "documents")
os.environ["JWT_SECRET"] = "test-secret-for-memory-tests"
os.environ["MEMORY_DIR"] = os.path.join(_tmp, "memory")

from fastapi.testclient import TestClient  # noqa: E402

from app import config as app_config  # noqa: E402
from app.ai import StreamChunk  # noqa: E402
from app.main import app  # noqa: E402
from app.memory import config as mem_cfg  # noqa: E402
from app.memory.capture import capture_assistant_reply, capture_user_message  # noqa: E402
from app.memory.cli import run as cli_run  # noqa: E402
from app.memory.commands import match_command  # noqa: E402
from app.memory.consolidate import consolidate  # noqa: E402
from app.memory.extract import (  # noqa: E402
    ExtractionResult,
    LLMExtractor,
    RuleExtractor,
    Turn,
    parse_extraction_json,
)
from app.memory.inject import build_memory_block  # noqa: E402
from app.memory.lint import lint  # noqa: E402
from app.memory.scheduler import MemoryScheduler, apply_extraction  # noqa: E402
from app.memory.store import Store  # noqa: E402
from app.skills.integration import MEMORY_PROTOCOL_BLOCK, build_system_prompt  # noqa: E402


def _store() -> Store:
    return Store(app_config.settings.memory_dir)


# --------------------------------------------------------------------------
# helpers (mirror test_smoke.py patterns)
# --------------------------------------------------------------------------

class ScriptedProvider:
    def __init__(self, turns):
        self.turns = list(turns)
        self.calls: list[list[dict]] = []

    async def stream_chat(self, messages, tools, model_config):
        self.calls.append(list(messages))
        for chunk in self.turns.pop(0):
            yield chunk

    async def chat(self, messages, tools, model_config):
        return SimpleNamespace(content="Scripted title")


def _owner_headers(client):
    status = client.get("/api/auth/status").json()
    if status["needs_setup"]:
        client.post("/api/auth/setup", json={"username": "owner", "password": "test1234"})
    login = client.post("/api/auth/login", json={"username": "owner", "password": "test1234"})
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['token']}"}


def _make_conv(client, headers, name="Mem"):
    created = client.post(
        "/api/models",
        json={
            "name": name,
            "provider": "openai_compatible",
            "model_name": f"scripted-{name.lower()}",
            "base_url": "http://127.0.0.1:1/v1",
            "enabled": True,
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    model_id = created.json()["id"]
    conv = client.post("/api/conversations", json={"title": "t", "model_id": model_id}, headers=headers)
    return conv.json()["id"]


def _seed_atom(store, user_id, text="User prefers dark mode", entity="user", confidence=0.95, persona_delta=True):
    atoms = [
        {
            "text": text,
            "entity": entity,
            "kind": "preference",
            "salience": 0.8,
            "confidence": confidence,
            "tags": ["preference"],
            "source_turn": 1,
        }
    ]
    deltas = [{"text": text, "kind": "preference", "salience": 0.8}] if persona_delta else []
    apply_extraction(
        store,
        user_id,
        [ExtractionResult(atoms=atoms, persona_deltas=deltas)],
        [Turn(source_id="seed-1", ts="2026-01-01T00:00:00+00:00", role="user", content=text)],
    )


# --------------------------------------------------------------------------
# Step 1 — store
# --------------------------------------------------------------------------

def test_store_bootstraps_schema_and_layout(tmp_path):
    store = Store(tmp_path / "memory")
    d = store.ensure_user(42)
    assert (d / "schema.md").exists()
    assert (d / "index.md").exists()
    assert (d / "log.md").exists()
    assert (d / "scores.json").exists()
    assert (d / "inbox").is_dir() and (d / "archive").is_dir()
    assert (d / "pages/entities").is_dir() and (d / "pages/scenarios").is_dir()
    assert "atom-" in (d / "schema.md").read_text()


def test_store_inbox_roundtrip(tmp_path):
    store = Store(tmp_path / "memory")
    sid = store.write_inbox(1, role="user", content="hello", conversation_id=7, model="m")
    files = store.list_inbox(1)
    assert len(files) == 1 and files[0].stem == sid
    text = files[0].read_text()
    assert "hello" in text and "role: user" in text
    store.archive_inbox(1, files)
    assert store.count_inbox(1) == 0
    assert len(list((tmp_path / "memory/1/archive").glob("*.md"))) == 1


# --------------------------------------------------------------------------
# Step 2 — capture (deterministic, failure-isolated)
# --------------------------------------------------------------------------

def test_capture_writes_inbox_and_flags_important(tmp_path):
    store = Store(tmp_path / "memory")
    sid = capture_user_message(store, 1, message="Remember my deadline is Friday", conversation_id=3, model="m")
    assert sid is not None
    meta = yaml.safe_load(store.list_inbox(1)[0].read_text().split("---", 2)[1])
    assert meta["role"] == "user" and meta["important"] is True
    sid2 = capture_assistant_reply(store, 1, content="Got it.", conversation_id=3, model="m")
    assert sid2 is not None
    assert store.count_inbox(1) == 2


def test_capture_failure_is_isolated(tmp_path, monkeypatch):
    store = Store(tmp_path / "memory")

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(store, "write_inbox", boom)
    assert capture_user_message(store, 1, message="hi", conversation_id=1, model="m") is None
    assert capture_assistant_reply(store, 1, content="hi back", conversation_id=1, model="m") is None
    # no exception escaped — chat would continue


def test_chat_endpoint_captures_full_transcript_automatically(monkeypatch):
    """DoD: a session with zero user/model memory actions still lands the
    full transcript in inbox/ — capture is plumbing, not a model decision."""
    # Capture tests prove the WRITE; per-turn extraction is exercised by its
    # own test, so keep the async extraction from consuming inbox here.
    monkeypatch.setattr("app.memory.scheduler.MemoryScheduler.on_chat_finished", lambda self, *a, **k: None)
    fake = ScriptedProvider([[StreamChunk(content_delta="The answer is 42."), StreamChunk(finish_reason="stop")]])
    monkeypatch.setattr("app.main.get_provider", lambda provider_type: fake)

    with TestClient(app) as client:
        headers = _owner_headers(client)
        conv_id = _make_conv(client, headers)
        resp = client.post(
            "/api/chat/stream",
            json={"conversation_id": conv_id, "message": "What is 6 times 7?"},
            headers=headers,
        )
        assert resp.status_code == 200

        store = _store()
        files = store.list_inbox(1)
        assert len(files) == 2, [f.name for f in files]
        texts = [f.read_text() for f in files]
        assert any("What is 6 times 7?" in t and "role: user" in t for t in texts)
        assert any("The answer is 42." in t and "role: assistant" in t for t in texts)


def test_chat_endpoint_captures_even_when_model_errors(monkeypatch):
    class FailingProvider:
        async def stream_chat(self, messages, tools, model_config):
            raise RuntimeError("provider exploded")

        async def chat(self, messages, tools, model_config):
            return SimpleNamespace(content="title")

    monkeypatch.setattr("app.main.get_provider", lambda provider_type: FailingProvider())
    monkeypatch.setattr("app.memory.scheduler.MemoryScheduler.on_chat_finished", lambda self, *a, **k: None)
    with TestClient(app) as client:
        headers = _owner_headers(client)
        conv_id = _make_conv(client, headers)
        resp = client.post(
            "/api/chat/stream",
            json={"conversation_id": conv_id, "message": "make it fail"},
            headers=headers,
        )
        assert resp.status_code == 200
        store = _store()
        files = store.list_inbox(1)
        assert len(files) == 2  # user message + assistant (error) captured
        assert any("make it fail" in f.read_text() for f in files)
        assistant = [f for f in files if "role: assistant" in f.read_text()][0]
        meta = yaml.safe_load(assistant.read_text().split("---", 2)[1])
        assert meta["status"] == "error"  # extraction can ignore failed turns


def test_chat_endpoint_captures_tools_only_turn(monkeypatch):
    """assistant_turn_summary falls back to a compact tool-round summary
    when a turn only executed tools (no closing text)."""
    from app.memory.capture import assistant_turn_summary

    assert assistant_turn_summary("real text", "", []) == "real text"
    assert assistant_turn_summary("", "Error: boom", []) == "Error: boom"
    summary = assistant_turn_summary("", "", [{"name": "web_search"}, {"name": "web_search"}])
    assert summary == "[tool round: web_search, web_search — no closing text]"
    assert assistant_turn_summary("", "", []) == ""


# --------------------------------------------------------------------------
# Step 3 — extraction (scheduled; LLM-assisted with rule fallback)
# --------------------------------------------------------------------------

async def test_rule_extractor_makes_atoms():
    turns = [
        Turn(source_id="a1", ts="t", role="user", content="My name is Alice. I prefer dark mode."),
        Turn(source_id="a2", ts="t", role="assistant", content="Nice to meet you, Alice!"),
    ]
    result = await RuleExtractor().extract(1, turns)
    texts = [a["text"].lower() for a in result.atoms]
    assert any("alice" in t for t in texts)
    assert any("dark mode" in t for t in texts)
    kinds = {a["kind"] for a in result.atoms}
    assert "name" in kinds and "preference" in kinds
    assert all(a["confidence"] == 0.5 for a in result.atoms)  # gated out of injection


def test_llm_extractor_parses_json():
    payload = (
        '{"atoms": [{"text": "User is learning Rust", "entity": "user", "kind": "fact", '
        '"salience": 0.7, "confidence": 0.9, "tags": ["learning"], "source_turn": 1}], '
        '"scenarios": [{"title": "Borrow checker gotcha", "summary": "Lifetimes must be explicit.", "tags": ["rust"]}], '
        '"persona_deltas": [{"text": "User is a developer", "kind": "identity", "salience": 0.9}]}'
    )
    turns = [Turn(source_id="a1", ts="t", role="user", content="I am learning Rust")]
    result = parse_extraction_json(payload, turns)
    assert len(result.atoms) == 1 and result.atoms[0]["source_id"] == "a1"
    assert result.atoms[0]["confidence"] == 0.9
    assert len(result.scenarios) == 1 and result.scenarios[0]["title"] == "Borrow checker gotcha"
    assert result.persona_deltas[0]["kind"] == "identity"


async def test_llm_extractor_falls_back_to_rules_on_garbage():
    class GarbageProvider:
        async def chat(self, messages, tools, model_config):
            return SimpleNamespace(content="definitely not json {{{")

    extractor = LLMExtractor(GarbageProvider(), SimpleNamespace(model_name="m"), "m")
    result = await extractor.extract(1, [Turn(source_id="a1", ts="t", role="user", content="My name is Bob")])
    assert any("bob" in a["text"].lower() for a in result.atoms)


async def test_per_turn_extraction_uses_chat_model_immediately(tmp_path):
    """Command-Code-style path: a single 'Hi, im axo' turn is distilled
    immediately after the response by the SAME model that just answered —
    no turn-count threshold, no debounce, no pattern list."""
    import asyncio as _asyncio

    store = Store(tmp_path / "memory")
    capture_user_message(store, 1, message="Hi, im axo", conversation_id=1, model="deepseek-chat")
    capture_assistant_reply(store, 1, content="Nice to meet you, axo!", conversation_id=1, model="deepseek-chat")

    payload = (
        '{"atoms": [{"text": "User is named axo", "entity": "user", "kind": "name", '
        '"salience": 0.9, "confidence": 0.95, "tags": ["name"], "source_turn": 1}]}'
    )

    class ChatModelProvider:
        def __init__(self):
            self.calls = []

        async def chat(self, messages, tools, model_config):
            self.calls.append((messages, model_config))
            return SimpleNamespace(content=payload)

    fake = ChatModelProvider()
    model_cfg = SimpleNamespace(
        model_name="deepseek-chat", max_tokens=8192, temperature=0.7, thinking_enabled=True,
        api_key_env="DEEPSEEK_API_KEY", base_url="http://x", provider="openai_compatible",
    )
    scheduler = MemoryScheduler(store, enabled=True)
    scheduler.on_chat_finished(1, provider=fake, model_config=model_cfg)
    await _asyncio.gather(*list(scheduler._extract_tasks))

    scores = store.read_scores(1)
    atoms = scores["atoms"]
    assert len(atoms) == 1
    atom = next(iter(atoms.values()))
    assert "axo" in atom["text"] and atom["kind"] == "name" and atom["confirmed"] is True
    assert store.count_inbox(1) == 0  # consumed immediately
    assert "extract" in store.read_log(1)

    # the extraction call used the chat's own model, capped for speed
    used = fake.calls[0][1]
    assert used.model_name == "deepseek-chat"
    assert used.max_tokens == mem_cfg.EXTRACT_MAX_TOKENS
    assert used.thinking_enabled is False
    # and the extraction prompt carried the actual transcript
    assert "Hi, im axo" in fake.calls[0][0][-1]["content"]


async def test_extraction_produces_atoms_and_queues_persona(monkeypatch, tmp_path):
    """DoD: extraction produces L1 atoms; persona/working set (L3) is queued."""
    store = Store(tmp_path / "memory")
    capture_user_message(store, 1, message="I prefer dark mode for the UI", conversation_id=1, model="m")
    capture_assistant_reply(store, 1, content="Noted.", conversation_id=1, model="m")
    user_stem = store.list_inbox(1)[0].stem

    payload = (
        '{"atoms": [{"text": "User prefers dark mode for the UI", "entity": "user", "kind": "preference", '
        '"salience": 0.8, "confidence": 0.95, "tags": ["ui", "preference"], "source_turn": 1}], '
        '"persona_deltas": [{"text": "User prefers dark mode", "kind": "preference", "salience": 0.8}]}'
    )

    class FakeExtractProvider:
        async def chat(self, messages, tools, model_config):
            return SimpleNamespace(content=payload)

    async def fake_resolve(user_id):
        return FakeExtractProvider(), SimpleNamespace(model_name="m")

    monkeypatch.setattr("app.memory.scheduler.resolve_extract_model", fake_resolve)
    scheduler = MemoryScheduler(store, enabled=True)
    await scheduler._extract_user(1)

    scores = store.read_scores(1)
    atoms = scores["atoms"]
    assert len(atoms) == 1
    atom = next(iter(atoms.values()))
    assert atom["confirmed"] is True  # confidence >= 0.7 -> injectable
    assert atom["source_ids"] == [user_stem]
    assert len(scores["persona_deltas"]) == 1  # queued, NOT yet applied
    page = store.read_page(1, "pages/entities/user.md")
    assert page and atom["id"] in page and "User prefers dark mode" in page
    # inbox consumed -> archive
    assert store.count_inbox(1) == 0
    assert len(list((tmp_path / "memory/1/archive").glob("*.md"))) == 2
    assert "extract" in store.read_log(1)


# --------------------------------------------------------------------------
# Step 4 — injection (automatic, invisible, budget-capped)
# --------------------------------------------------------------------------

async def test_injection_working_set_and_budget_caps(tmp_path):
    store = Store(tmp_path / "memory")
    _seed_atom(store, 1)
    consolidate(store, 1)  # applies persona delta -> persona.md exists

    block = build_memory_block(store, 1, query="what about dark mode?")
    assert len(block) <= mem_cfg.MEMORY_DATA_MAX_CHARS
    assert "Persona" in block and "dark mode" in block
    assert "## Related" in block and "User prefers dark mode" in block

    # budget: protocol + data <= ~2.6k total context growth
    assert len(MEMORY_PROTOCOL_BLOCK) <= 600
    assert len(MEMORY_PROTOCOL_BLOCK) + len(block) <= 2600

    # unrelated query injects working set but no scored atoms
    block2 = build_memory_block(store, 1, query="quantum physics of parrots")
    assert "Persona" in block2
    assert "## Related" not in block2


async def test_injection_skips_unconfirmed(tmp_path):
    store = Store(tmp_path / "memory")
    _seed_atom(store, 1, text="User prefers dark mode", confidence=0.4, persona_delta=False)
    _seed_atom(store, 1, text="User lives in Berlin", confidence=0.95)
    consolidate(store, 1)
    block = build_memory_block(store, 1, query="dark mode berlin")
    assert "lives in Berlin" in block
    assert "prefers dark mode" not in block  # low-confidence atom stays out


async def test_injection_boost_persists_freq(tmp_path):
    store = Store(tmp_path / "memory")
    _seed_atom(store, 1)
    consolidate(store, 1)
    before = next(iter(store.read_scores(1)["atoms"].values()))["freq"]
    build_memory_block(store, 1, query="dark mode")
    after = next(iter(store.read_scores(1)["atoms"].values()))["freq"]
    assert after == before + 1  # spaced-repetition boost


def test_system_prompt_memory_footprint():
    plain = build_system_prompt("mock")
    assert "[MEMORY]" not in plain and "# Memory protocol" not in plain
    with_mem = build_system_prompt("mock", memory_block="## Persona\n- likes dark mode")
    assert "# Memory protocol" in with_mem and "[MEMORY]" in with_mem
    assert "likes dark mode" in with_mem


def test_fresh_session_injects_relevant_memory(monkeypatch):
    """DoD: a fresh session starts with relevant [MEMORY] injected — and the
    model gets it without any memory tool call or save action."""
    _seed_atom(_store(), 1, text="User prefers dark mode in the editor")

    fake = ScriptedProvider([[StreamChunk(content_delta="Sure."), StreamChunk(finish_reason="stop")]])
    monkeypatch.setattr("app.main.get_provider", lambda provider_type: fake)

    with TestClient(app) as client:
        headers = _owner_headers(client)
        conv_id = _make_conv(client, headers)
        resp = client.post(
            "/api/chat/stream",
            json={"conversation_id": conv_id, "message": "should I switch my editor to dark mode?"},
            headers=headers,
        )
        assert resp.status_code == 200
        system = fake.calls[0][0]["content"]
        assert "[MEMORY]" in system
        assert "dark mode in the editor" in system
        assert "# Memory protocol" in system


# --------------------------------------------------------------------------
# Step 5 — consolidate + lint + commands + CLI
# --------------------------------------------------------------------------

async def test_consolidate_applies_persona_and_rebuilds_scores(tmp_path):
    store = Store(tmp_path / "memory")
    _seed_atom(store, 1)
    # simulate a manual Obsidian edit: a new bullet appears on the entity page
    page = store.read_page(1, "pages/entities/user.md")
    store.write_page(1, "pages/entities/user.md", page + "- `atom-manual01` User works from home.\n")

    report = consolidate(store, 1)
    assert report["persona_applied"] == 1
    persona = store.read_page(1, "pages/persona.md")
    assert "prefers dark mode" in persona

    scores = store.read_scores(1)
    assert scores["persona_deltas"] == []  # queue drained
    texts = {a["text"] for a in scores["atoms"].values()}
    assert "User works from home." in texts  # manual edit folded back in

    index = store.read_index(1)
    assert "pages/entities/user.md" in index  # catalog up to date
    assert "consolidate" in store.read_log(1)


async def test_consolidate_merges_duplicates_and_supersedes(tmp_path):
    store = Store(tmp_path / "memory")
    _seed_atom(store, 1, text="User prefers dark mode")
    _seed_atom(store, 1, text="User prefers light theme", confidence=0.95, entity="user")
    # simulate a manual edit / second extractor pass: duplicate bullet on the page
    page = store.read_page(1, "pages/entities/user.md")
    store.write_page(1, "pages/entities/user.md", page + "- `atom-dup0001` User prefers dark mode.\n")

    report = consolidate(store, 1)
    assert report["merged"] >= 1
    assert report["superseded"] == 1

    scores = store.read_scores(1)
    texts = {a["text"] for a in scores["atoms"].values()}
    assert "User prefers light theme" in texts
    old = [a for a in scores["atoms"].values() if "prefers dark mode" in a["text"]]
    assert old and old[0]["superseded"]  # older kept, marked
    page = store.read_page(1, "pages/entities/user.md")
    assert "[superseded" in page


async def test_lint_finds_issues(tmp_path):
    store = Store(tmp_path / "memory")
    _seed_atom(store, 1)
    consolidate(store, 1)
    # force index drift + an orphan page
    store.write_page(1, "pages/entities/orphan.md", "# Orphan\n\n## Facts\n")
    store.write_index(1, "# Memory Index\n\n")
    findings = lint(store, 1)
    checks = {f["check"] for f in findings}
    assert "index" in checks
    assert "orphan" in checks


def test_chat_command_anchoring():
    assert match_command("consolidate memory") == "consolidate"
    assert match_command("Consolidate memory") == "consolidate"  # case-insensitive
    assert match_command("lint memory") == "lint"
    assert match_command("lint memory.") == "lint"
    assert match_command("Should I consolidate my memory?") is None
    assert match_command("remember this") is None
    assert match_command("consolidate memories") is None


async def test_cli_status_lint_and_reset(tmp_path):
    store = Store(tmp_path / "memory")
    store.ensure_user(5)
    assert store.reset_user(5) is True
    assert not store.user_exists(5)
    assert store.reset_user(5) is False
    # CLI smoke against the app memory dir (read-only ops + harmless reset)
    assert await cli_run(["status", "--user", "1"]) == 0
    assert await cli_run(["lint", "--user", "1"]) == 0
    assert await cli_run(["reset", "--user", "99999", "--yes"]) == 0  # no store: harmless


def test_memory_api_status_and_actions(monkeypatch):
    """The Memory panel endpoints: status, delete atom, extract, consolidate,
    lint, reset — all per-user, any authenticated user."""
    monkeypatch.setattr("app.memory.scheduler.MemoryScheduler.on_chat_finished", lambda self, *a, **k: None)

    store = _store()
    store.reset_user(1)  # deterministic start for user 1 (owner)
    _seed_atom(store, 1, text="User is named axo", entity="user")
    capture_user_message(store, 1, message="pending message", conversation_id=9, model="m")

    with TestClient(app) as client:
        headers = _owner_headers(client)

        # status
        status = client.get("/api/memory/status", headers=headers)
        assert status.status_code == 200
        body = status.json()
        assert body["atoms_count"] == 1 and body["confirmed_count"] == 1
        assert body["inbox_pending"] == 1
        assert body["atoms"][0]["text"] == "User is named axo"
        assert body["atoms"][0]["confirmed"] is True
        assert isinstance(body["log"], list)

        # lint
        lint_res = client.post("/api/memory/lint", headers=headers)
        assert lint_res.status_code == 200
        assert isinstance(lint_res.json()["findings"], list)

        # extract the pending turn (rule fallback: "pending message" -> no atoms)
        ext = client.post("/api/memory/extract", headers=headers)
        assert ext.status_code == 200
        assert ext.json()["processed"] == 1 and ext.json()["pending"] == 0

        # delete the atom
        atom_id = body["atoms"][0]["id"]
        dele = client.delete(f"/api/memory/atoms/{atom_id}", headers=headers)
        assert dele.status_code == 200
        assert client.get("/api/memory/status", headers=headers).json()["atoms_count"] == 0
        assert client.delete(f"/api/memory/atoms/{atom_id}", headers=headers).status_code == 404

        # consolidate
        cons = client.post("/api/memory/consolidate", headers=headers)
        assert cons.status_code == 200
        assert "atoms_before" in cons.json()

        # reset wipes everything
        reset = client.delete("/api/memory", headers=headers)
        assert reset.status_code == 200 and reset.json()["status"] == "reset"
        empty = client.get("/api/memory/status", headers=headers).json()
        assert empty["atoms_count"] == 0 and empty["inbox_pending"] == 0 and empty["log"] == []


def test_memory_api_requires_auth():
    assert client_get_unauth("/api/memory/status") == 401
    assert client_get_unauth("/api/memory", method="DELETE") == 401


def client_get_unauth(path, method="GET"):
    import httpx
    from fastapi.testclient import TestClient as TC

    with TC(app) as client:
        resp = client.request(method, path)
        return resp.status_code


def test_openai_chat_and_image_generation_via_real_provider(tmp_path, monkeypatch):
    """Regression: non-streaming OpenAICompatibleProvider.chat() must work —
    it feeds memory extraction (and title generation). This broke silently
    before (AttributeError -> rule fallback -> 'nothing stored'). Also covers
    generate_image, which was corrupted by the same code splice."""
    import asyncio as _asyncio
    import json as _json
    import threading as _threading
    from http.server import BaseHTTPRequestHandler as _H, HTTPServer as _S
    from types import SimpleNamespace as _NS

    from app.ai import OpenAICompatibleProvider, AIResponse, ImageGenerationResult

    EXTRACT_JSON = (
        '{"atoms": [{"text": "User is named axo", "entity": "user", "kind": "name", '
        '"salience": 0.9, "confidence": 0.95, "tags": ["name"], "source_turn": 1}]}'
    )

    class Handler(_H):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            req = _json.loads(body or b"{}")
            if self.path.startswith("/v1/images/generations"):
                data = _json.dumps({
                    "created": 1,
                    "data": [{"b64_json": "QUJD", "revised_prompt": "revised"}],
                })
            else:
                Handler.chat_prompt = req.get("messages", [])
                data = _json.dumps({"choices": [{"message": {"role": "assistant", "content": EXTRACT_JSON}}]})
            raw = data.encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *a):
            pass

    srv = _S(("127.0.0.1", 0), Handler)
    _threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    monkeypatch.setattr(app_config.settings, "deepseek_api_key", "dummy")

    model = _NS(
        model_name="deepseek-chat", max_tokens=8192, temperature=0.7, thinking_enabled=False,
        api_key_env="DEEPSEEK_API_KEY", base_url=f"http://127.0.0.1:{port}/v1",
        provider="openai_compatible",
    )
    provider = OpenAICompatibleProvider()

    async def run():
        # 1) chat() feeds the per-turn extraction path end-to-end
        store = Store(tmp_path / "memory")
        capture_user_message(store, 1, message="Hi, im axo", conversation_id=1, model="deepseek-chat")
        capture_assistant_reply(store, 1, content="Hey Axo!", conversation_id=1, model="deepseek-chat")
        sched = MemoryScheduler(store, enabled=True)
        sched.on_chat_finished(1, provider=provider, model_config=model)
        await _asyncio.gather(*list(sched._extract_tasks))
        atoms = store.read_scores(1)["atoms"]
        assert len(atoms) == 1 and "axo" in next(iter(atoms.values()))["text"]
        assert store.count_inbox(1) == 0
        # the extraction prompt carried the transcript
        assert "Hi, im axo" in Handler.chat_prompt[-1]["content"]

        # 2) chat() itself (title-style) returns a proper AIResponse
        resp = await provider.chat([{"role": "user", "content": "title me"}], [], model)
        assert isinstance(resp, AIResponse)
        assert "User is named axo" in resp.content

        # 3) generate_image() returns a proper result (was corrupted by the
        #    tool-call code splice and raised AttributeError/returned a tuple)
        img = await provider.generate_image("a cat", model, "512x512", 1)
        assert isinstance(img, ImageGenerationResult)
        assert img.images == ["data:image/png;base64,QUJD"]
        assert img.revised_prompt == "revised"

    _asyncio.run(run())
    srv.shutdown()


def test_chat_stream_emits_memory_saved_pill(monkeypatch):
    """After a reply, the SSE stream carries a `memory_saved` event (the
    data behind the tool-pill notification) when extraction saved something."""
    import json as _json
    from types import SimpleNamespace as _NS

    EXTRACT = (
        '{"atoms": [{"text": "User is named axo", "entity": "user", "kind": "name", '
        '"salience": 0.9, "confidence": 0.95, "tags": ["name"], "source_turn": 1}]}'
    )

    class MemProvider:
        def __init__(self):
            self.turns = [[StreamChunk(content_delta="Hey Axo!"), StreamChunk(finish_reason="stop")]]

        async def stream_chat(self, messages, tools, model_config):
            for chunk in self.turns.pop(0):
                yield chunk

        async def chat(self, messages, tools, model_config):
            return _NS(content=EXTRACT)

    monkeypatch.setattr("app.main.get_provider", lambda provider_type: MemProvider())
    with TestClient(app) as client:
        headers = _owner_headers(client)
        conv_id = _make_conv(client, headers)
        resp = client.post(
            "/api/chat/stream",
            json={"conversation_id": conv_id, "message": "Hi, im axo"},
            headers=headers,
        )
        assert resp.status_code == 200
        events = [
            _json.loads(line[len("data: "):])
            for line in resp.text.splitlines()
            if line.startswith("data: ") and line.strip() != "data: [DONE]"
        ]
        saved = [e for e in events if e.get("type") == "memory_saved"]
        assert saved, [e.get("type") for e in events]
        assert saved[0]["items"][0] == {"kind": "atom", "text": "User is named axo"}
