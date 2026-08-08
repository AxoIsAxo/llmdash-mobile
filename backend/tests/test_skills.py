"""P11 — modular skill system tests.

Cover: loader auto-discovery (all builtins + manifest fields), registry
listing by source/category/scope, execution-time scope enforcement, and the
per-user skill API (disable → hidden from tools + blocked at execution,
custom config round-trip, unknown skill 404).

Same isolation pattern as test_entitlements.py: env set at module level,
app imports lazy.
"""

import os
import tempfile
from types import SimpleNamespace

_tmp = tempfile.mkdtemp(prefix="llmdash_skill_test_")
os.environ.setdefault("DATABASE_PATH", os.path.join(_tmp, "test.db"))
os.environ.setdefault("UPLOADS_DIR", os.path.join(_tmp, "uploads"))
os.environ.setdefault("DOCUMENTS_DIR", os.path.join(_tmp, "documents"))
os.environ.setdefault("MEMORY_DIR", os.path.join(_tmp, "memory"))
os.environ.setdefault("JWT_SECRET", "test-secret-skills")

from fastapi.testclient import TestClient  # noqa: E402

EXPECTED_BUILTINS = {
    "web_search", "web_scrape", "render_html", "render_svg", "render_video",
    "run_command", "get_theme", "patch_theme", "reset_theme",
    "get_user_css", "patch_user_css", "append_user_css", "set_user_css",
    "edit_document", "git_clone", "git_status", "git_diff", "git_commit",
    "git_push", "git_pr",
}


def _owner_headers(client):
    status = client.get("/api/auth/status").json()
    if status["needs_setup"]:
        client.post("/api/auth/setup", json={"username": "owner", "password": "test1234"})
    login = client.post("/api/auth/login", json={"username": "owner", "password": "test1234"})
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['token']}"}


def test_loader_auto_discovers_all_builtins():
    from app.skills import register_builtins
    from app.skills.registry import skill_registry

    register_builtins()
    names = set(skill_registry.names())
    assert EXPECTED_BUILTINS.issubset(names), f"missing: {EXPECTED_BUILTINS - names}"

    skill = skill_registry.get("git_push")
    manifest = skill.to_manifest()
    assert manifest["name"] == "git_push"
    assert manifest["source"] == "builtin"
    assert manifest["category"] == "git"
    assert manifest["scopes"] == ["git"]
    assert manifest["entitlement"] == "git_access"
    assert manifest["version"] and manifest["author"]

    web = skill_registry.get("web_search")
    assert web.to_manifest()["scopes"] == ["web"]
    assert skill_registry.get("run_command").to_manifest()["scopes"] == ["sandbox"]


def test_registry_listing_and_scope_enforcement():
    from app.skills.base import Skill
    from app.skills.registry import skill_registry

    # Listing filters by source / category / scope.
    builtins = skill_registry.list_skills(source="builtin")
    assert len(builtins) == len(EXPECTED_BUILTINS)
    git_skills = skill_registry.list_skills(category="git")
    assert {s.name for s in git_skills} == {f"git_{op}" for op in ("clone", "status", "diff", "commit", "push", "pr")}
    sandbox_scoped = skill_registry.list_skills(scope="sandbox")
    assert {s.name for s in sandbox_scoped} == {"run_command"}

    # Scope enforcement: a skill declaring a scope outside the allowed set is refused.
    class ScopedSkill(Skill):
        name = "test_scoped"
        description = "d"
        category = "test"
        scopes = ("git", "sandbox")
        input_schema = {"type": "object", "properties": {}, "required": []}

        async def execute(self, arguments: dict, **context) -> str:
            return "ok"

    skill_registry.register(ScopedSkill())
    try:
        import asyncio

        denied = asyncio.run(skill_registry.execute("test_scoped", {}, allowed_scopes=set()))
        assert "requires scopes" in denied and "sandbox" in denied
        ok = asyncio.run(skill_registry.execute("test_scoped", {}, allowed_scopes={"git", "sandbox"}))
        assert ok == "ok"
    finally:
        skill_registry.unregister("test_scoped")


def test_per_user_skill_disable_and_config(monkeypatch):
    from app.main import app

    with TestClient(app) as client:
        headers = _owner_headers(client)

        # Baseline: web_search visible.
        tools = client.get("/api/tools", headers=headers).json()
        assert "web_search" in {t["name"] for t in tools}

        # Manifest list carries per-user state.
        skills = client.get("/api/skills", headers=headers).json()
        by_name = {s["name"]: s for s in skills}
        assert by_name["web_search"]["user_enabled"] is True
        assert by_name["web_search"]["scopes"] == ["web"]
        assert by_name["git_clone"]["category"] == "git"

        try:
            # Disable web_search for the owner.
            resp = client.put("/api/skills/web_search/enable", json={"enabled": False}, headers=headers)
            assert resp.status_code == 200

            tools = client.get("/api/tools", headers=headers).json()
            assert "web_search" not in {t["name"] for t in tools}
            # web_scrape is a DIFFERENT skill and stays enabled (per-skill scoping).
            assert "web_scrape" in {t["name"] for t in tools}

            skills = client.get("/api/skills", headers=headers).json()
            by_name = {s["name"]: s for s in skills}
            assert by_name["web_search"]["user_enabled"] is False

            # Custom config round-trip.
            cfg = client.put("/api/skills/web_search/config", json={"config": {"max_results": 5}}, headers=headers)
            assert cfg.status_code == 200
            skills = client.get("/api/skills", headers=headers).json()
            assert {s["name"]: s for s in skills}["web_search"]["config"] == {"max_results": 5}

            # Unknown skill -> 404.
            assert client.put("/api/skills/nope/enable", json={"enabled": False}, headers=headers).status_code == 404

            # Chat-stream execution guard blocks the disabled skill.
            from app.ai import StreamChunk
            from app.skills.registry import skill_registry

            created = client.post(
                "/api/models",
                json={
                    "name": "SkillTest",
                    "provider": "openai_compatible",
                    "model_name": "scripted-skilltest",
                    "base_url": "http://127.0.0.1:1/v1",
                    "enabled": True,
                },
                headers=headers,
            )
            assert created.status_code == 201
            model_id = created.json()["id"]
            conv = client.post("/api/conversations", json={"title": "t", "model_id": model_id}, headers=headers)
            conv_id = conv.json()["id"]

            class Scripted:
                def __init__(self, turns):
                    self.turns = list(turns)

                async def stream_chat(self, messages, tools, model_config):
                    for chunk in self.turns.pop(0):
                        yield chunk

                async def chat(self, messages, tools, model_config):
                    return SimpleNamespace(content="t")

            fake = Scripted([
                [StreamChunk(tool_calls=[{"id": "c1", "name": "web_search", "arguments": {"q": "x"}}], finish_reason="tool_calls")],
                [StreamChunk(content_delta="Final answer."), StreamChunk(finish_reason="stop")],
            ])
            monkeypatch.setattr("app.main.get_provider", lambda provider_type: fake)
            try:
                resp = client.post(
                    "/api/chat/stream",
                    json={"conversation_id": conv_id, "message": "find it"},
                    headers=headers,
                )
                assert resp.status_code == 200
                msgs = client.get(f"/api/conversations/{conv_id}/messages", headers=headers).json()
                tool_msgs = [m for m in msgs if m["role"] == "tool"]
                assert tool_msgs, "expected a tool round"
                assert "__TOOL_ERROR__: Tool 'web_search' is disabled for your account." in tool_msgs[0]["content"]
            finally:
                monkeypatch.setattr("app.main.get_provider", lambda provider_type: None)
        finally:
            # Restore the default so later test modules are unaffected.
            client.put("/api/skills/web_search/enable", json={"enabled": True}, headers=headers)

        tools = client.get("/api/tools", headers=headers).json()
        assert "web_search" in {t["name"] for t in tools}
