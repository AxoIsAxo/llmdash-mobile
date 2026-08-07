"""Smoke tests for the LLMDash backend.

Run with:  pip install -r requirements-dev.txt && pytest
"""

import os
import tempfile
from types import SimpleNamespace

_tmp = tempfile.mkdtemp(prefix="llmdash_test_")
os.environ["DATABASE_PATH"] = os.path.join(_tmp, "test.db")
os.environ["UPLOADS_DIR"] = os.path.join(_tmp, "uploads")
os.environ["DOCUMENTS_DIR"] = os.path.join(_tmp, "documents")
os.environ["JWT_SECRET"] = "test-secret-for-smoke-tests"

from fastapi.testclient import TestClient  # noqa: E402

from app.ai import StreamChunk  # noqa: E402
from app.main import (  # noqa: E402
    NO_ACTION_NUDGE,
    NO_ANSWER_NOTE,
    FINAL_ANSWER_NUDGE,
    MAX_TOOL_ROUNDS,
    _bump_model_max_tokens,
    _classify_no_action_turn,
    _looks_like_deliberation,
    _looks_like_filler,
    _repair_tool_history,
    app,
)


def test_looks_like_deliberation():
    assert _looks_like_deliberation(
        "Let me fetch the actual site to analyze its design (colors, fonts, layout) so I can replicate it faithfully."
    ) is True
    assert _looks_like_deliberation("I'll check the current theme and then update it.") is True
    assert _looks_like_deliberation("First, I need to look at the page.") is True
    assert _looks_like_deliberation("The answer is 42.") is False
    assert _looks_like_deliberation("Sure! Here's the result: the theme is dark.") is False
    assert _looks_like_deliberation("") is False
    assert _looks_like_deliberation("Hello! How can I help you today?") is False
    # Long narration that never acts — the exact failure the user reported.
    long_plan = (
        "I'll do both: capture the current Telegram design into a file, and check out "
        "extrovert.redforged.eu for the new design. Let me gather everything in parallel: "
        "The login page text is minimal — let me dig into the actual HTML/CSS of "
        "extrovert.redforged.eu to extract its design tokens: The page uses CSS variables "
        "(--primary, --secondary, etc.) with a dark theme. Let me grab the actual "
        "stylesheet to extract the exact design tokens:"
    )
    assert _looks_like_deliberation(long_plan) is True
    # Second reported failure: another long narration that never acts.
    long_plan_2 = (
        "I'll do both: first grab your current theme so I can save it, and check out that "
        "site's design.The site only exposed a login page to the scraper, so let me pull "
        "its actual HTML/CSS directly to read the design tokens:curl isn't available in "
        "this sandbox — I'll use python instead:Odd — the sandbox is missing its usual "
        "tools. Let me check what's available:It's a minimal Alpine box — wget is there. "
        "Fetching the site now:"
    )
    assert _looks_like_deliberation(long_plan_2) is True
    # Third reported failure: narration before a run_command attempt.
    long_plan_3 = (
        "The user wants two things: save the current Telegram-like design to a file in their "
        "documents, and create a new design inspired by extrovert.redforged.eu. Let me first "
        "get the current theme to save it, and scrape the website to see what it looks like. "
        "I can do these in parallel: get_theme + web_scrape of extrovert.redforged.eu. For "
        "saving to documents — I have edit_document tool which creates downloadable file "
        "artifacts. Let me start by getting the current theme and scraping the site in "
        "parallel.The website extrovert.redforged.eu just shows a login page. Let me try to "
        "get more design info. Let me use run_command with curl to grab the HTML and look "
        "for colors, fonts. The site is a login page — let me pull its raw HTML/CSS to "
        "extract the actual design details (colors, fonts, layout)."
    )
    assert _looks_like_deliberation(long_plan_3) is True
    # Long real answers must NOT be flagged.
    long_answer = (
        "I'll explain the architecture: the backend is FastAPI with an async SQLAlchemy "
        "session, and the frontend is React 19 with Vite. Messages stream over SSE "
        "endpoints, and tool calls round-trip through the same channel. The database is "
        "SQLite with aiosqlite, and the whole thing runs in Docker with a SearXNG "
        "sidecar for web search."
    )
    assert _looks_like_deliberation(long_answer) is False


def test_repair_tool_history_drops_orphan_tool_messages():
    # Corrupted history: assistant only lists round-2 calls, but tool
    # results for both rounds were stored.
    history = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "let me search", "tool_calls_json": '[{"id":"c2","name":"web_search","arguments":{}}]'},
        {"role": "tool", "tool_call_id": "c1", "tool_name": "web_search", "content": "old result"},
        {"role": "tool", "tool_call_id": "c2", "tool_name": "web_search", "content": "new result"},
    ]
    repaired = _repair_tool_history(history)
    assert len(repaired) == 3  # orphan c1 dropped
    assert repaired[1]["tool_calls_json"] == '[{"id": "c2", "name": "web_search", "arguments": {}}]'
    assert [m["tool_call_id"] for m in repaired if m["role"] == "tool"] == ["c2"]


def test_repair_tool_history_strips_unanswered_calls():
    # Assistant called a tool but the run was cancelled before results saved.
    history = [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "calling", "tool_calls_json": '[{"id":"c1","name":"run_command","arguments":{}}]'},
    ]
    repaired = _repair_tool_history(history)
    assert repaired[-1]["tool_calls_json"] is None
    assert repaired[-1]["content"] == "calling"


def test_repair_tool_history_keeps_valid_sequence():
    history = [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "", "tool_calls_json": '[{"id":"c1","name":"render_html","arguments":{}}]'},
        {"role": "tool", "tool_call_id": "c1", "tool_name": "render_html", "content": "HTML_RENDER:abc"},
        {"role": "assistant", "content": "done"},
    ]
    repaired = _repair_tool_history(history)
    assert len(repaired) == 4
    assert repaired[1]["tool_calls_json"] is not None
    assert repaired[2]["role"] == "tool"


def test_full_flow():
    with TestClient(app) as client:
        # --- auth status / setup / login ---
        status = client.get("/api/auth/status")
        assert status.status_code == 200
        if status.json()["needs_setup"]:
            setup = client.post("/api/auth/setup", json={"username": "owner", "password": "test1234"})
            assert setup.status_code == 200
            token = setup.json()["token"]
        else:
            # An earlier test module sharing this DB already ran setup.
            login = client.post("/api/auth/login", json={"username": "owner", "password": "test1234"})
            assert login.status_code == 200
            token = login.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        me = client.get("/api/auth/me", headers=headers)
        assert me.status_code == 200 and me.json()["role"] == "owner"

        again = client.post("/api/auth/setup", json={"username": "owner2", "password": "test1234"})
        assert again.status_code == 400

        login = client.post("/api/auth/login", json={"username": "owner", "password": "test1234"})
        assert login.status_code == 200
        assert client.post("/api/auth/login", json={"username": "owner", "password": "nope"}).status_code == 401

        # --- models + reorder validation ---
        created = client.post(
            "/api/models",
            json={
                "name": "Mock",
                "provider": "openai_compatible",
                "model_name": "mock-chat",
                "base_url": "http://127.0.0.1:1/v1",
                "enabled": True,
            },
            headers=headers,
        )
        assert created.status_code == 201
        model_id = created.json()["id"]

        assert client.put("/api/models/reorder", json={"model_ids": [model_id]}, headers=headers).status_code == 200
        assert client.put("/api/models/reorder", json={"model_ids": [999999]}, headers=headers).status_code == 400
        assert client.put("/api/models/reorder", json={"model_ids": [model_id, model_id]}, headers=headers).status_code == 400

        # --- env updates restricted to known keys ---
        bad_env = client.post("/api/config/env", json={"updates": {"DATABASE_PATH": "/tmp/evil"}}, headers=headers)
        assert bad_env.status_code == 400

        # --- documents upload + response shape ---
        upload = client.post(
            "/api/documents/upload",
            files={"file": ("hello.md", b"# Hello", "text/markdown")},
            headers=headers,
        )
        assert upload.status_code == 200

        docs = client.get("/api/documents", headers=headers)
        assert docs.status_code == 200
        row = docs.json()[0]
        assert "file_size" in row and "content" in row

        # --- chat attachment path validation (no arbitrary paths) ---
        conv = client.post("/api/conversations", json={"title": "t", "model_id": model_id}, headers=headers)
        conv_id = conv.json()["id"]

        forged = client.post(
            "/api/chat/stream",
            json={
                "conversation_id": conv_id,
                "message": "x",
                "attachments": [{"filename": "k.png", "file_type": ".png", "file_path": "/etc/hosts"}],
            },
            headers=headers,
        )
        assert forged.status_code == 400


# ---------------------------------------------------------------------------
# Agent-loop helpers (the "thinks a lot, then stops" fixes)
# ---------------------------------------------------------------------------


def test_classify_no_action_turn():
    # Real answers are accepted.
    assert _classify_no_action_turn("The answer is 42.", "", None) is None
    assert _classify_no_action_turn("Here are the results of the search: ...", "", None) is None
    assert _classify_no_action_turn("Here is a partial answer even though tokens ran out.", "", "length") is None
    # Plan/filler text is not an answer.
    assert _classify_no_action_turn("Let me check the theme first.", "", None) == "deliberation"
    assert _classify_no_action_turn("I'll search for that now.", "", None) == "deliberation"
    # Silent turns (only thinking) are not answers.
    assert _classify_no_action_turn("", "thinking hard about it...", None) == "silent"
    assert _classify_no_action_turn("", "", None) == "silent"
    # Budget exhaustion with no substance is a budget cutoff.
    assert _classify_no_action_turn("", "", "length") == "budget"
    assert _classify_no_action_turn("Let me try another query.", "", "max_tokens") == "budget"


def test_registry_passes_conversation_context():
    """Skills that declare _conversation_id / _current_user receive them; skills
    that don't declare them get them filtered out (so no signature errors)."""
    import asyncio

    from app.skills.base import Skill
    from app.skills.registry import SkillRegistry

    seen = {}

    class WithCtx(Skill):
        name = "with_ctx"
        description = "d"
        input_schema = {"type": "object", "properties": {}, "required": []}

        async def execute(self, arguments: dict, _conversation_id: int = 0, _current_user: dict = None) -> str:
            seen["conv"] = _conversation_id
            seen["user"] = _current_user
            return "ok"

    class NoCtx(Skill):
        name = "no_ctx"
        description = "d"
        input_schema = {"type": "object", "properties": {}, "required": []}

        async def execute(self, arguments: dict) -> str:
            seen["no_ctx"] = True
            return "ok"

    reg = SkillRegistry()
    reg.register(WithCtx())
    reg.register(NoCtx())

    async def run():
        await reg.execute("with_ctx", {}, _current_user={"user_id": 7}, _conversation_id=42)
        await reg.execute("no_ctx", {}, _current_user={"user_id": 7}, _conversation_id=42)

    asyncio.run(run())
    assert seen["conv"] == 42
    assert seen["user"] == {"user_id": 7}
    assert seen["no_ctx"] is True


def test_sandbox_pool_eviction_and_names(monkeypatch):
    import asyncio

    import app.sandbox as sb

    assert sb._container_name(42) == "llmdash-sandbox-42"

    calls = []

    async def fake_docker(*args, timeout=60.0):
        calls.append(args)
        return 0, "", ""

    monkeypatch.setattr(sb, "_docker", fake_docker)
    sb._sandboxes.clear()
    import time as _t
    # `last_used: 0.0` would only be stale once the process has run > TTL;
    # make it stale relative to the current monotonic clock instead.
    sb._sandboxes[1] = {"container": "c1", "last_used": _t.monotonic() - sb.SANDBOX_IDLE_TTL - 60}  # stale
    sb._sandboxes[2] = {"container": "c2", "last_used": 999999999.0}  # fresh
    asyncio.run(sb._sweep())
    assert ("rm", "-f", "c1") in calls
    assert 1 not in sb._sandboxes
    assert 2 in sb._sandboxes

    # Pool cap: with a full pool, the least-recently-used one is evicted.
    import time as _time
    _now = _time.monotonic()
    sb._sandboxes.clear()
    sb._sandboxes.update({
        i: {"container": f"c{i}", "last_used": _now - i}  # all recent; conv 17 oldest
        for i in range(1, sb.SANDBOX_MAX_POOL + 2)
    })
    asyncio.run(sb._sweep())
    assert len(sb._sandboxes) <= sb.SANDBOX_MAX_POOL
    assert (sb.SANDBOX_MAX_POOL + 1) not in sb._sandboxes  # oldest evicted
    assert 1 in sb._sandboxes                              # most recent kept


def test_looks_like_filler():
    assert _looks_like_filler("Let me search for that.") is True
    assert _looks_like_filler("I'll look it up now") is True
    assert _looks_like_filler("Searching...") is True
    assert _looks_like_filler("Here is what I found.") is False
    assert _looks_like_filler("") is False
    assert _looks_like_filler("x") is False


def test_bump_model_max_tokens():
    class FakeModel:
        model_name = "mock-chat"
        max_tokens = 4096
        temperature = 0.7
        thinking_enabled = True
        thinking_budget_tokens = 4000
        api_key_env = "DEEPSEEK_API_KEY"
        base_url = "http://127.0.0.1:1/v1"
        provider = "openai_compatible"

    bumped = _bump_model_max_tokens(FakeModel(), 8192)
    assert bumped.max_tokens == 8192
    assert bumped.model_name == "mock-chat"
    assert bumped.thinking_enabled is True
    assert bumped.api_key_env == "DEEPSEEK_API_KEY"
    # The original model row is untouched.
    assert FakeModel.max_tokens == 4096


class ScriptedProvider:
    """Async provider that replays scripted StreamChunk turns.

    Each element of `turns` is either a list of StreamChunk or a callable
    receiving the current messages and returning the chunks. Records every
    message list and model config it was called with for assertions.
    """

    def __init__(self, turns):
        self.turns = list(turns)
        self.calls: list[list[dict]] = []
        self.model_configs = []
        self.tools_list: list[list] = []

    def _next(self, messages, model_config):
        self.calls.append(list(messages))
        self.model_configs.append(model_config)
        turn = self.turns.pop(0)
        return turn(messages) if callable(turn) else turn

    async def stream_chat(self, messages, tools, model_config):
        self.tools_list.append(list(tools))
        for chunk in self._next(messages, model_config):
            yield chunk

    async def chat(self, messages, tools, model_config):
        return SimpleNamespace(content="Scripted title")


def _sse_events(text: str) -> list[dict]:
    import json as _json
    events = []
    for line in text.splitlines():
        if line.startswith("data: ") and line.strip() != "data: [DONE]":
            events.append(_json.loads(line[len("data: "):]))
    return events


def _owner_headers(client):
    status = client.get("/api/auth/status").json()
    if status["needs_setup"]:
        client.post("/api/auth/setup", json={"username": "owner", "password": "test1234"})
    login = client.post("/api/auth/login", json={"username": "owner", "password": "test1234"})
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['token']}"}


def _make_conv(client, headers, name):
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
    return conv.json()["id"], model_id


def _last_assistant(client, headers, conv_id):
    msgs = client.get(f"/api/conversations/{conv_id}/messages", headers=headers).json()
    return [m for m in msgs if m["role"] == "assistant"][-1]


def test_agent_nudges_deliberation_turn_until_it_acts(monkeypatch):
    """A plan-only reply ("Let me check...") must NOT be the final answer:
    the loop nudges the model, it calls a tool, and the stored answer is the
    follow-up text — not the filler."""
    fake = ScriptedProvider([
        # Turn 1: only a plan, no tool call.
        [StreamChunk(content_delta="Let me check that for you..."), StreamChunk(finish_reason="stop")],
        # Turn 2: after the nudge, the model finally acts (unknown tool ->
        # registry returns a fast error, no network/docker needed).
        [StreamChunk(tool_calls=[{"id": "c1", "name": "no_such_tool", "arguments": {}}], finish_reason="tool_calls")],
        # Turn 3: final answer after the (failed) tool round.
        [StreamChunk(content_delta="I could not find it, here is why."), StreamChunk(finish_reason="stop")],
    ])
    monkeypatch.setattr("app.main.get_provider", lambda provider_type: fake)

    with TestClient(app) as client:
        headers = _owner_headers(client)
        conv_id, _ = _make_conv(client, headers, "Nudge")

        resp = client.post(
            "/api/chat/stream",
            json={"conversation_id": conv_id, "message": "find me x"},
            headers=headers,
        )
        assert resp.status_code == 200
        events = _sse_events(resp.text)

        # The model was nudged exactly once (2nd stream call) with the action nudge.
        assert any(m["role"] == "user" and m["content"] == NO_ACTION_NUDGE for m in fake.calls[1])
        # The nudge turn produced a tool call.
        assert any(e["type"] == "tool_calls" for e in events)
        # The follow-up turn saw the tool result in history.
        assert any(m["role"] == "tool" for m in fake.calls[2])

        last = _last_assistant(client, headers, conv_id)
        assert last["content"] == "I could not find it, here is why."
        assert "Let me check" not in last["content"]


def test_agent_recovers_silent_thinking_turn(monkeypatch):
    """A turn that produced ONLY thinking (no text, no tool call) must be
    retried, and the thinking is preserved in the stored message."""
    fake = ScriptedProvider([
        [StreamChunk(reasoning_content_delta="I should look this up carefully..."), StreamChunk(finish_reason="stop")],
        [StreamChunk(content_delta="The answer is 42."), StreamChunk(finish_reason="stop")],
    ])
    monkeypatch.setattr("app.main.get_provider", lambda provider_type: fake)

    with TestClient(app) as client:
        headers = _owner_headers(client)
        conv_id, _ = _make_conv(client, headers, "Silent")

        resp = client.post(
            "/api/chat/stream",
            json={"conversation_id": conv_id, "message": "think then answer"},
            headers=headers,
        )
        assert resp.status_code == 200

        last = _last_assistant(client, headers, conv_id)
        assert last["content"] == "The answer is 42."
        assert "look this up carefully" in (last["reasoning_content"] or "")


def test_agent_raises_budget_when_thinking_burns_max_tokens(monkeypatch):
    """finish_reason='length' with no output must trigger ONE retry with a
    larger max_tokens, then succeed."""
    fake = ScriptedProvider([
        # Turn 1: reasoning burned the whole budget — nothing else came out.
        [StreamChunk(reasoning_content_delta="lots and lots of thinking..."), StreamChunk(finish_reason="length")],
        # Turn 2: with the bigger budget the model answers.
        [StreamChunk(content_delta="Now I can actually answer."), StreamChunk(finish_reason="stop")],
    ])
    monkeypatch.setattr("app.main.get_provider", lambda provider_type: fake)

    with TestClient(app) as client:
        headers = _owner_headers(client)
        conv_id, _ = _make_conv(client, headers, "Budget")

        resp = client.post(
            "/api/chat/stream",
            json={"conversation_id": conv_id, "message": "big task"},
            headers=headers,
        )
        assert resp.status_code == 200

        # The retry got a bumped max_tokens (4096 -> 8192).
        assert fake.model_configs[1].max_tokens > fake.model_configs[0].max_tokens
        last = _last_assistant(client, headers, conv_id)
        assert last["content"] == "Now I can actually answer."


def test_agent_bails_with_note_after_repeated_deliberation(monkeypatch):
    """After the bounded nudges the loop gives up with a clear note — the
    plan/filler text must NOT leak through as the final answer."""
    fake = ScriptedProvider([
        [StreamChunk(content_delta="Let me check that for you..."), StreamChunk(finish_reason="stop")],
        [StreamChunk(content_delta="I'll search for it right away."), StreamChunk(finish_reason="stop")],
        [StreamChunk(content_delta="First, I need to look at the data."), StreamChunk(finish_reason="stop")],
    ])
    monkeypatch.setattr("app.main.get_provider", lambda provider_type: fake)

    with TestClient(app) as client:
        headers = _owner_headers(client)
        conv_id, _ = _make_conv(client, headers, "Bail")

        print("CONV_ID:", repr(conv_id), flush=True)
        resp = client.post(
            "/api/chat/stream",
            json={"conversation_id": conv_id, "message": "do the thing"},
            headers=headers,
        )
        assert resp.status_code == 200

        # Exactly 2 nudges were sent, one per failed turn.
        assert sum(1 for m in fake.calls[1] if m["role"] == "user" and m["content"] == NO_ACTION_NUDGE) == 1
        assert sum(1 for m in fake.calls[2] if m["role"] == "user" and m["content"] == NO_ACTION_NUDGE) == 2

        last = _last_assistant(client, headers, conv_id)
        assert last["content"] == NO_ANSWER_NOTE
        assert "Let me check" not in last["content"]


def test_thinking_model_gets_headroom_upfront(monkeypatch):
    """A thinking model configured with a self-defeating budget (max_tokens
    4096, thinking 4000) must get a raised max_tokens on the very first call."""
    fake = ScriptedProvider([
        [StreamChunk(content_delta="Done."), StreamChunk(finish_reason="stop")],
    ])
    monkeypatch.setattr("app.main.get_provider", lambda provider_type: fake)

    with TestClient(app) as client:
        headers = _owner_headers(client)
        created = client.post(
            "/api/models",
            json={
                "name": "Thinker",
                "provider": "openai_compatible",
                "model_name": "scripted-thinker",
                "base_url": "http://127.0.0.1:1/v1",
                "enabled": True,
                "max_tokens": 4096,
                "thinking_enabled": True,
                "thinking_budget_tokens": 4000,
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        conv_id = created.json()["id"]
        conv = client.post("/api/conversations", json={"title": "t", "model_id": conv_id}, headers=headers)

        resp = client.post(
            "/api/chat/stream",
            json={"conversation_id": conv.json()["id"], "message": "think"},
            headers=headers,
        )
        assert resp.status_code == 200

        # headroom was 96 (< 1024) -> the very first call uses 8192.
        assert fake.model_configs[0].max_tokens == 8192
        last = _last_assistant(client, headers, conv.json()["id"])
        assert last["content"] == "Done."


def test_reasoning_survives_tool_rounds(monkeypatch):
    """The exact reported regression: the model thinks (STEP1), calls a tool,
    then thinks again (STEP2) and answers. The FULL reasoning must be present
    in the stored message and in the final content event — earlier turns'
    thinking must not vanish after the tool round."""
    fake = ScriptedProvider([
        # Turn 1: deep thinking, then a tool call.
        [StreamChunk(reasoning_content_delta="STEP1: I must find the theme colors..."),
         StreamChunk(tool_calls=[{"id": "c1", "name": "no_such_tool", "arguments": {}}], finish_reason="tool_calls")],
        # Turn 2: final answer with its own reasoning.
        [StreamChunk(reasoning_content_delta="STEP2: the results are in, I can answer."),
         StreamChunk(content_delta="Here is the answer."),
         StreamChunk(finish_reason="stop")],
    ])
    monkeypatch.setattr("app.main.get_provider", lambda provider_type: fake)

    with TestClient(app) as client:
        headers = _owner_headers(client)
        conv_id, _ = _make_conv(client, headers, "Reasoning")

        resp = client.post(
            "/api/chat/stream",
            json={"conversation_id": conv_id, "message": "redesign ui"},
            headers=headers,
        )
        assert resp.status_code == 200
        events = _sse_events(resp.text)

        final = [e for e in events if e["type"] == "content"][-1]
        assert final.get("done") is True, f"final content event not marked done: {final}"
        assert "STEP1" in (final.get("reasoning_content") or ""), f"STEP1 missing from final event: {final.get('reasoning_content')!r}"
        assert "STEP2" in (final.get("reasoning_content") or ""), f"STEP2 missing from final event: {final.get('reasoning_content')!r}"

        last = _last_assistant(client, headers, conv_id)
        assert "STEP1" in (last["reasoning_content"] or ""), f"STEP1 missing from stored message: {last['reasoning_content']!r}"
        assert "STEP2" in (last["reasoning_content"] or ""), f"STEP2 missing from stored message: {last['reasoning_content']!r}"
        assert last["content"] == "Here is the answer."


def test_after_tools_silent_turn_gets_answer_nudge(monkeypatch):
    """Once tools have executed, a silent follow-up turn must be nudged to
    ANSWER (not to call more tools), and the final answer must arrive."""
    from app.main import ANSWER_NUDGE
    fake = ScriptedProvider([
        # Turn 1: act — call a tool.
        [StreamChunk(tool_calls=[{"id": "c1", "name": "no_such_tool", "arguments": {}}], finish_reason="tool_calls")],
        # Turn 2: silence after the tool round (no content, no tool call).
        [StreamChunk(finish_reason="stop")],
        # Turn 3: after the ANSWER nudge, the model finally answers.
        [StreamChunk(content_delta="OK here is the summary of what I found."), StreamChunk(finish_reason="stop")],
    ])
    monkeypatch.setattr("app.main.get_provider", lambda provider_type: fake)

    with TestClient(app) as client:
        headers = _owner_headers(client)
        conv_id, _ = _make_conv(client, headers, "AnswerNudge")

        resp = client.post(
            "/api/chat/stream",
            json={"conversation_id": conv_id, "message": "research then answer"},
            headers=headers,
        )
        assert resp.status_code == 200

        # The nudge for turn 3 was the ANSWER nudge (tools had already run).
        assert any(m["role"] == "user" and m["content"] == ANSWER_NUDGE for m in fake.calls[2])
        last = _last_assistant(client, headers, conv_id)
        assert last["content"] == "OK here is the summary of what I found."


def test_no_narration_in_stream(monkeypatch):
    """Narration between tool rounds must NEVER reach the client — only the
    finished answer is streamed as content (plan text, "Let me..." narration,
    and tool-round preamble are all discarded by the runtime)."""
    fake = ScriptedProvider([
        # Turn 1: model plans out loud, no tools -> nudged, text discarded
        [StreamChunk(content_delta="Let me check the docs for that..."), StreamChunk(finish_reason="stop")],
        # Turn 2: tool round with narration before the call -> discarded
        [StreamChunk(content_delta="Let me fetch the page now."),
         StreamChunk(tool_calls=[{"id": "c1", "name": "no_such_tool", "arguments": {}}], finish_reason="tool_calls")],
        # Turn 3: the finished answer -> the ONLY text the client sees
        [StreamChunk(content_delta="The final answer."), StreamChunk(finish_reason="stop")],
    ])
    monkeypatch.setattr("app.main.get_provider", lambda provider_type: fake)

    with TestClient(app) as client:
        headers = _owner_headers(client)
        conv_id, _ = _make_conv(client, headers, "Narr")

        resp = client.post(
            "/api/chat/stream",
            json={"conversation_id": conv_id, "message": "do the thing"},
            headers=headers,
        )
        assert resp.status_code == 200
        events = _sse_events(resp.text)
        deltas = "".join(e.get("content", "") for e in events if e["type"] == "content_delta")
        assert "Let me" not in deltas, deltas
        assert "The final answer." in deltas
        last = _last_assistant(client, headers, conv_id)
        assert last["content"] == "The final answer."


def test_agent_finishes_with_answer_after_tool_cap(monkeypatch):
    """When the model burns every tool round without ever writing an answer,
    the runtime forces one final text-only round so the user gets a finished
    reply instead of tool pills and silence ('the AI just stopped')."""
    turns = []
    for i in range(MAX_TOOL_ROUNDS):
        turns.append([
            StreamChunk(tool_calls=[{"id": f"c{i}", "name": "no_such_tool", "arguments": {"q": str(i)}}], finish_reason="tool_calls"),
        ])
    turns.append([StreamChunk(content_delta="Here is the finished answer."), StreamChunk(finish_reason="stop")])
    fake = ScriptedProvider(turns)
    monkeypatch.setattr("app.main.get_provider", lambda provider_type: fake)

    with TestClient(app) as client:
        headers = _owner_headers(client)
        conv_id, _ = _make_conv(client, headers, "Cap")

        resp = client.post(
            "/api/chat/stream",
            json={"conversation_id": conv_id, "message": "do it"},
            headers=headers,
        )
        assert resp.status_code == 200
        last = _last_assistant(client, headers, conv_id)
        assert last["content"] == "Here is the finished answer."
        assert "Aborted" not in last["content"]
        events = _sse_events(resp.text)
        assert any(e["type"] == "tool_calls" for e in events)
        # the forced final round was the last stream call, with tools disabled
        assert fake.tools_list[-1] == []  # tools list empty on the final round
        assert FINAL_ANSWER_NUDGE in fake.calls[-1][-1]["content"]


def test_parallel_tool_execution_per_round(monkeypatch):
    """All tool calls in one round execute concurrently and each emits its
    own tool_start/tool_result, mapping results back by id (agent-style)."""
    fake = ScriptedProvider([
        [StreamChunk(tool_calls=[
            {"id": "c1", "name": "no_such_tool", "arguments": {"q": "a"}},
            {"id": "c2", "name": "no_such_tool", "arguments": {"q": "b"}},
        ], finish_reason="tool_calls")],
        [StreamChunk(content_delta="Done."), StreamChunk(finish_reason="stop")],
    ])
    monkeypatch.setattr("app.main.get_provider", lambda provider_type: fake)

    with TestClient(app) as client:
        headers = _owner_headers(client)
        conv_id, _ = _make_conv(client, headers, "Par")

        resp = client.post(
            "/api/chat/stream",
            json={"conversation_id": conv_id, "message": "do both"},
            headers=headers,
        )
        assert resp.status_code == 200
        events = _sse_events(resp.text)
        starts = [e["id"] for e in events if e["type"] == "tool_start"]
        results = [e["id"] for e in events if e["type"] == "tool_result"]
        assert sorted(starts) == ["c1", "c2"]
        assert sorted(results) == ["c1", "c2"]
        last = _last_assistant(client, headers, conv_id)
        assert last["content"] == "Done."


def test_thinking_timeline_is_chronological(monkeypatch):
    """The thinking timeline stores reasoning and tool markers in call order,
    so the UI can show where in the thinking each tool was called."""
    fake = ScriptedProvider([
        [StreamChunk(reasoning_content_delta="I should search the web first."),
         StreamChunk(tool_calls=[{"id": "c1", "name": "no_such_tool", "arguments": {}}], finish_reason="tool_calls")],
        [StreamChunk(reasoning_content_delta="The result is clear now."),
         StreamChunk(content_delta="The answer is 42."), StreamChunk(finish_reason="stop")],
    ])
    monkeypatch.setattr("app.main.get_provider", lambda provider_type: fake)

    with TestClient(app) as client:
        headers = _owner_headers(client)
        conv_id, _ = _make_conv(client, headers, "Timeline")
        resp = client.post(
            "/api/chat/stream",
            json={"conversation_id": conv_id, "message": "find it"},
            headers=headers,
        )
        assert resp.status_code == 200
        msgs = client.get(f"/api/conversations/{conv_id}/messages", headers=headers).json()
        last = [m for m in msgs if m["role"] == "assistant"][-1]
        tl = last["thinking_json"]
        assert tl is not None
        assert [e["type"] for e in tl] == ["reasoning", "tool", "reasoning"]
        assert tl[0]["text"] == "I should search the web first."
        assert tl[1]["id"] == "c1"
        assert tl[2]["text"] == "The result is clear now."
        # the live SSE content event carries the timeline, so the UI
        # interleaves without needing a reload
        content_events = [e for e in _sse_events(resp.text) if e["type"] == "content"]
        assert content_events and content_events[-1].get("thinking_json")
        assert [e["type"] for e in content_events[-1]["thinking_json"]] == ["reasoning", "tool", "reasoning"]
