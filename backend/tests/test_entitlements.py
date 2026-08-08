"""P9 — fine-grained subscription entitlement tests.

Cover: Free-plan defaults (all-on), per-plan entitlement overrides, server-side
gating of tools (registry) and endpoints (403), the chat-stream execution
guard, per-model access allowlist, and the /me entitlements payload.

Env vars are set at module level (same pattern as test_smoke.py) and all
`app.*` imports are lazy so whichever test module imports app.main first
decides the shared DB — this module only guarantees it is a fresh temp DB.
"""

import os
import tempfile
from types import SimpleNamespace

_tmp = tempfile.mkdtemp(prefix="llmdash_ent_test_")
os.environ.setdefault("DATABASE_PATH", os.path.join(_tmp, "test.db"))
os.environ.setdefault("UPLOADS_DIR", os.path.join(_tmp, "uploads"))
os.environ.setdefault("DOCUMENTS_DIR", os.path.join(_tmp, "documents"))
os.environ.setdefault("MEMORY_DIR", os.path.join(_tmp, "memory"))
os.environ.setdefault("JWT_SECRET", "test-secret-entitlements")

from fastapi.testclient import TestClient  # noqa: E402


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


def _sse_events(text: str) -> list[dict]:
    import json as _json
    events = []
    for line in text.splitlines():
        if line.startswith("data: ") and line.strip() != "data: [DONE]":
            events.append(_json.loads(line[len("data: "):]))
    return events


def _last_assistant(client, headers, conv_id):
    msgs = client.get(f"/api/conversations/{conv_id}/messages", headers=headers).json()
    return [m for m in msgs if m["role"] == "assistant"][-1]


class _ScriptedProvider:
    def __init__(self, turns):
        self.turns = list(turns)
        self.calls = []

    async def stream_chat(self, messages, tools, model_config):
        self.calls.append(list(messages))
        turn = self.turns.pop(0)
        for chunk in turn:
            yield chunk

    async def chat(self, messages, tools, model_config):
        return SimpleNamespace(content="Scripted title")


LOCKED = {
    "web_search", "document_editor", "render", "sandbox", "git_access",
    "image_generation", "file_upload", "voice_input",
    "conversation_branching", "memory", "theme_editing",
}


def test_entitlement_defaults_and_registry_filtering():
    from app.entitlements import DEFAULT_ENTITLEMENTS
    from app.skills import register_builtins
    from app.skills.registry import skill_registry

    register_builtins()
    # All defaults on -> every gated skill visible.
    all_on = dict(DEFAULT_ENTITLEMENTS)
    names = {d["name"] for d in skill_registry.get_tool_definitions(all_on)}
    for expect in ("web_search", "web_scrape", "run_command", "git_clone", "edit_document", "render_svg", "get_theme"):
        assert expect in names, expect
    # Turn a few off -> those tools disappear, others stay.
    off = dict(DEFAULT_ENTITLEMENTS)
    off["web_search"] = False
    off["sandbox"] = False
    off["git_access"] = False
    names = {d["name"] for d in skill_registry.get_tool_definitions(off)}
    assert "web_search" not in names and "web_scrape" not in names
    assert "run_command" not in names
    assert "git_clone" not in names and "git_pr" not in names
    assert "render_html" in names and "edit_document" in names
    assert skill_registry.entitlement_of("web_search") == "web_search"
    assert skill_registry.entitlement_of("git_push") == "git_access"
    assert skill_registry.entitlement_of("run_command") == "sandbox"


def test_entitlement_gating(monkeypatch):
    from app.entitlements import DEFAULT_ENTITLEMENTS
    from app.main import app

    with TestClient(app) as client:
        headers = _owner_headers(client)

        # Free plan defaults: everything enabled.
        plans = client.get("/api/subscriptions/plans", headers=headers).json()
        free = next(p for p in plans if p["name"] == "Free")
        assert free["entitlements"] == DEFAULT_ENTITLEMENTS

        # /me carries the merged entitlements (Free -> all on).
        me = client.get("/api/auth/me", headers=headers).json()
        assert me["entitlements"]["web_search"] is True

        # Create a plan with a subset locked off.
        created = client.post(
            "/api/subscriptions/plans",
            json={
                "name": "LockedP9",
                "price_sats": 0,
                "duration_days": 30,
                "entitlements": {k: False for k in LOCKED},
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        plan_id = created.json()["id"]

        conv_id, model_id = _make_conv(client, headers, "EntLocked")

        try:
            # Free plan defaults hold until the owner subscribes to the locked plan.
            me = client.get("/api/auth/me", headers=headers).json()
            assert me["entitlements"]["web_search"] is True

            sub = client.post("/api/subscriptions/subscribe", json={"plan_id": plan_id}, headers=headers)
            assert sub.status_code == 201, sub.text
            sub_id = sub.json()["subscription_id"]

            try:
                # /me now reflects the locked plan.
                me = client.get("/api/auth/me", headers=headers).json()
                for key in LOCKED:
                    assert me["entitlements"][key] is False, key
                assert me["entitlements"]["tts"] is True

                # Tools list hides locked skills.
                tools = client.get("/api/tools", headers=headers).json()
                tool_names = {t["name"] for t in tools}
                for locked in ("web_search", "web_scrape", "run_command", "git_clone", "edit_document", "render_svg", "get_theme"):
                    assert locked not in tool_names, locked

                # Endpoint gates -> 403.
                assert client.get("/api/memory/status", headers=headers).status_code == 403
                assert client.get("/api/git/repos", headers=headers).status_code == 403
                assert client.post("/api/chat/upload", files={"file": ("a.txt", b"hi", "text/plain")}, headers=headers).status_code == 403
                assert client.post("/api/chat/transcribe", files={"file": ("a.webm", b"x", "audio/webm")}, headers=headers).status_code == 403
                assert client.get("/api/documents", headers=headers).status_code == 403
                assert client.put("/api/auth/css", json={"css": "a { color: red; }"}, headers=headers).status_code == 403
                assert client.put("/api/auth/theme", json={"spec": {"preset": "dark"}}, headers=headers).status_code == 403
                assert client.post(f"/api/conversations/{conv_id}/branch", json={"message_index": 0}, headers=headers).status_code == 403

                # Image generation gate.
                image_resp = client.post(
                    "/api/chat/image",
                    json={"conversation_id": conv_id, "model_id": model_id, "prompt": "x", "size": "1024x1024", "n": 1},
                    headers=headers,
                )
                assert image_resp.status_code == 403

                # Chat-stream execution guard: a scripted model that still tries
                # web_search gets a plan error as the tool result.
                from app.ai import StreamChunk

                fake = _ScriptedProvider([
                    [StreamChunk(tool_calls=[{"id": "c1", "name": "web_search", "arguments": {"q": "x"}}], finish_reason="tool_calls")],
                    [StreamChunk(content_delta="Second-turn answer."), StreamChunk(finish_reason="stop")],
                ])
                monkeypatch.setattr("app.main.get_provider", lambda provider_type: fake)
                try:
                    resp = client.post(
                        "/api/chat/stream",
                        json={"conversation_id": conv_id, "message": "find it"},
                        headers=headers,
                    )
                    assert resp.status_code == 200
                    last = _last_assistant(client, headers, conv_id)
                    assert last["content"] == "Second-turn answer."
                    msgs = client.get(f"/api/conversations/{conv_id}/messages", headers=headers).json()
                    tool_msgs = [m for m in msgs if m["role"] == "tool"]
                    assert tool_msgs, "expected a tool round"
                    assert "__TOOL_ERROR__: Tool 'web_search' is not included in your current plan." in tool_msgs[0]["content"]
                finally:
                    monkeypatch.setattr("app.main.get_provider", lambda provider_type: None)

                # Per-model access: deny the scripted model for this plan.
                set_limits = client.put(
                    f"/api/subscriptions/plans/{plan_id}/limits",
                    json=[{"model_id": model_id, "token_limit": None, "image_limit": None, "allowed": False}],
                    headers=headers,
                )
                assert set_limits.status_code == 200

                # A non-admin user on the locked plan cannot see or use the denied model.
                created_user = client.post(
                    "/api/auth/users",
                    json={"username": "entuser", "password": "test1234", "role": "user"},
                    headers=headers,
                )
                assert created_user.status_code in (200, 201), created_user.text
                user_login = client.post("/api/auth/login", json={"username": "entuser", "password": "test1234"})
                user_headers = {"Authorization": f"Bearer {user_login.json()['token']}"}
                try:
                    user_sub = client.post("/api/subscriptions/subscribe", json={"plan_id": plan_id}, headers=user_headers)
                    assert user_sub.status_code == 201, user_sub.text

                    user_models = client.get("/api/models", headers=user_headers).json()
                    assert all(m["id"] != model_id for m in user_models), "denied model must be hidden for the user"

                    user_conv = client.post("/api/conversations", json={"title": "t", "model_id": model_id}, headers=user_headers)
                    assert user_conv.status_code in (200, 201), user_conv.text
                    denied_stream = client.post(
                        "/api/chat/stream",
                        json={"conversation_id": user_conv.json()["id"], "message": "hi"},
                        headers=user_headers,
                    )
                    assert denied_stream.status_code == 403
                finally:
                    client.delete(f"/api/auth/users/{created_user.json()['id']}", headers=headers)

                # Admins still see every model (allowlist applies to non-admins).
                owner_models = client.get("/api/models", headers=headers).json()
                assert any(m["id"] == model_id for m in owner_models)
            finally:
                client.post(f"/api/subscriptions/cancel/{sub_id}", headers=headers)
        finally:
            client.delete(f"/api/subscriptions/plans/{plan_id}", headers=headers)

        # After cleanup the owner is back on Free: tools fully visible again.
        tools = client.get("/api/tools", headers=headers).json()
        assert "web_search" in {t["name"] for t in tools}
