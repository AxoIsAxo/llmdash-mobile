"""P12 — skill marketplace tests.

Cover: install from a local git repo (file://), manifest + entry validation
rejections, approval review gate (regular users don't see/use unapproved
skills), uninstall (removes code + registry + per-user rows), and the
skill_marketplace entitlement gate.

Installs write into the real marketplace package dir — every test cleans up
in finally so the source tree stays pristine.
"""

import json
import os
import subprocess
import tempfile
from types import SimpleNamespace

_tmp = tempfile.mkdtemp(prefix="llmdash_market_test_")
os.environ.setdefault("DATABASE_PATH", os.path.join(_tmp, "test.db"))
os.environ.setdefault("UPLOADS_DIR", os.path.join(_tmp, "uploads"))
os.environ.setdefault("DOCUMENTS_DIR", os.path.join(_tmp, "documents"))
os.environ.setdefault("MEMORY_DIR", os.path.join(_tmp, "memory"))
os.environ.setdefault("JWT_SECRET", "test-secret-marketplace")

from fastapi.testclient import TestClient  # noqa: E402

SKILL_PY = '''\
from app.skills.base import Skill


class HelloMarketSkill(Skill):
    name = "hello_market"
    description = "Says hello from the marketplace."
    version = "1.0.0"
    author = "tester"
    category = "general"
    scopes = ("web",)
    entitlement = None
    input_schema = {"type": "object", "properties": {}, "required": []}

    async def execute(self, arguments, **context):
        return "hello from marketplace"
'''

MULTI_SKILL_PY = '''\
from app.skills.base import Skill
from .helper import helper_text


class MultiMarketSkill(Skill):
    name = "multi_market"
    description = "Multi-file marketplace skill with a relative import."
    version = "1.0.0"
    author = "tester"
    category = "general"
    scopes = ("web",)
    entitlement = None
    input_schema = {"type": "object", "properties": {}, "required": []}

    async def execute(self, arguments, **context):
        return helper_text()
'''

HELPER_PY = '''\
def helper_text():
    return "multi-file helper works"
'''


MANIFEST = {
    "name": "hello_market",
    "version": "1.0.0",
    "author": "tester",
    "description": "Says hello from the marketplace.",
    "category": "general",
    "scopes": ["web"],
    "entitlement": None,
    "input_schema": {"type": "object", "properties": {}, "required": []},
    "entry": "skill.py",
}

MULTI_MANIFEST = {**MANIFEST, "name": "multi_market", "entry": "skill.py"}


def _make_repo(name="hello_market", manifest=None, extra_files=None):
    """Create a local git repo with manifest.json + entry module."""
    repo = tempfile.mkdtemp(prefix=f"llmdash_market_repo_{name}_")
    with open(os.path.join(repo, "manifest.json"), "w") as f:
        json.dump(manifest or MANIFEST, f, indent=2)
    if name == "multi_market":
        with open(os.path.join(repo, "skill.py"), "w") as f:
            f.write(MULTI_SKILL_PY)
        for fname, content in (extra_files or {}).items():
            with open(os.path.join(repo, fname), "w") as f:
                f.write(content)
    else:
        with open(os.path.join(repo, "skill.py"), "w") as f:
            f.write(SKILL_PY)
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    subprocess.run(["git", "init", "-q", repo], check=True)
    subprocess.run(["git", "-C", repo, "add", "."], check=True)
    subprocess.run(["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "skill"], check=True, env=env)
    return repo


def _owner_headers(client):
    status = client.get("/api/auth/status").json()
    if status["needs_setup"]:
        client.post("/api/auth/setup", json={"username": "owner", "password": "test1234"})
    login = client.post("/api/auth/login", json={"username": "owner", "password": "test1234"})
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['token']}"}


def _skill_names(client, headers):
    return {t["name"] for t in client.get("/api/tools", headers=headers).json()}


def test_marketplace_install_validate_approve_uninstall(monkeypatch):
    from app.main import app
    from app.skills import marketplace_store

    with TestClient(app) as client:
        headers = _owner_headers(client)
        repo = _make_repo()
        url = "file://" + repo
        try:
            # --- validation rejections ---
            # unknown scope
            bad = _make_repo("bad_scope", {**MANIFEST, "name": "bad_scope", "scopes": ["shell"]})
            resp = client.post("/api/marketplace/install", json={"url": "file://" + bad}, headers=headers)
            assert resp.status_code == 400, resp.text
            assert "scopes" in resp.json()["detail"]

            # name collision with a builtin
            clash = _make_repo("web_search", {**MANIFEST, "name": "web_search"})
            resp = client.post("/api/marketplace/install", json={"url": "file://" + clash}, headers=headers)
            assert resp.status_code == 400 and "already exists" in resp.json()["detail"]

            # missing manifest
            nomani = tempfile.mkdtemp(prefix="llmdash_nomani_")
            subprocess.run(["git", "init", "-q", nomani], check=True)
            resp = client.post("/api/marketplace/install", json={"url": "file://" + nomani}, headers=headers)
            assert resp.status_code == 400 and "no manifest.json" in resp.json()["detail"]

            # --- install ---
            assert "hello_market" not in _skill_names(client, headers)
            resp = client.post("/api/marketplace/install", json={"url": url}, headers=headers)
            assert resp.status_code == 201, resp.text
            assert resp.json()["approved"] is False

            # owner/admin sees it and can execute it
            assert "hello_market" in _skill_names(client, headers)
            overview = client.get("/api/marketplace", headers=headers).json()
            installed = {i["name"]: i for i in overview["installed"]}
            assert installed["hello_market"]["approved"] is False
            assert installed["hello_market"]["source_url"] == url

            import asyncio
            from app.skills.registry import skill_registry
            result = asyncio.run(skill_registry.execute(
                "hello_market", {}, allowed_scopes=skill_registry.builtin_scope_union(),
            ))
            assert result == "hello from marketplace"
            # scope enforcement still applies to marketplace skills
            denied = asyncio.run(skill_registry.execute("hello_market", {}, allowed_scopes=set()))
            assert "requires scopes" in denied

            # --- restart simulation: the loader must keep stamping source and
            # the review gate must keep holding (state-based, not class attr) ---
            from app.skills import register_builtins
            register_builtins()  # re-discovers from the marketplace package dir
            reloaded = skill_registry.get("hello_market")
            assert reloaded.to_manifest()["source"] == "marketplace"
            assert "hello_market" in marketplace_store.unapproved_names()

            # regular users must NOT see or use the unapproved skill
            created = client.post("/api/auth/users", json={"username": "mktuser", "password": "test1234", "role": "user"}, headers=headers)
            assert created.status_code in (200, 201), created.text
            login = client.post("/api/auth/login", json={"username": "mktuser", "password": "test1234"})
            user_headers = {"Authorization": f"Bearer {login.json()['token']}"}
            try:
                assert "hello_market" not in _skill_names(client, user_headers)
                skill_list = client.get("/api/skills", headers=user_headers).json()
                assert "hello_market" not in {s["name"] for s in skill_list}
                # cannot enable/config an unapproved skill (404, no existence leak)
                assert client.put("/api/skills/hello_market/enable", json={"enabled": False}, headers=user_headers).status_code == 404
                assert client.put("/api/skills/hello_market/config", json={"config": {}}, headers=user_headers).status_code == 404
                # cannot approve or uninstall (role gate)
                assert client.post("/api/marketplace/hello_market/approve", headers=user_headers).status_code == 403
                assert client.delete("/api/marketplace/hello_market", headers=user_headers).status_code == 403

                # --- review gate: approve ---
                resp = client.post("/api/marketplace/hello_market/approve", headers=headers)
                assert resp.status_code == 200
                assert client.get("/api/marketplace", headers=headers).json()["installed"][0]["approved"] is True

                # regular user now sees the approved skill
                assert "hello_market" in _skill_names(client, user_headers)
            finally:
                client.delete(f"/api/auth/users/{created.json()['id']}", headers=headers)

            # per-user rows are revocable: disable before uninstall
            assert client.put("/api/skills/hello_market/enable", json={"enabled": False}, headers=headers).status_code == 200
        finally:
            # --- uninstall (cleanup) ---
            resp = client.delete("/api/marketplace/hello_market", headers=headers)
            assert resp.status_code == 200, resp.text
            assert "hello_market" not in _skill_names(client, headers)
            assert not (marketplace_store.MARKETPLACE_DIR / "hello_market.py").exists()
            assert marketplace_store.load_state() == {}
            # double uninstall -> 404
            assert client.delete("/api/marketplace/hello_market", headers=headers).status_code == 404


def test_marketplace_multi_file_skill():
    """Skills with helper modules + relative imports install and execute."""
    from app.main import app
    from app.skills import marketplace_store

    with TestClient(app) as client:
        headers = _owner_headers(client)
        repo = _make_repo("multi_market", MULTI_MANIFEST, extra_files={"helper.py": HELPER_PY})
        try:
            resp = client.post("/api/marketplace/install", json={"url": "file://" + repo}, headers=headers)
            assert resp.status_code == 201, resp.text
            assert "multi_market" in _skill_names(client, headers)

            import asyncio
            from app.skills.registry import skill_registry
            result = asyncio.run(skill_registry.execute(
                "multi_market", {}, allowed_scopes=skill_registry.builtin_scope_union(),
            ))
            assert result == "multi-file helper works"
        finally:
            resp = client.delete("/api/marketplace/multi_market", headers=headers)
            assert resp.status_code == 200, resp.text
            assert not (marketplace_store.MARKETPLACE_DIR / "multi_market").exists()


def test_marketplace_corrupt_state_fails_closed(monkeypatch):
    """A corrupt state.json must NOT leak installed skills to regular users."""
    from app.main import app
    from app.skills import marketplace_store

    with TestClient(app) as client:
        headers = _owner_headers(client)
        repo = _make_repo()
        try:
            resp = client.post("/api/marketplace/install", json={"url": "file://" + repo}, headers=headers)
            assert resp.status_code == 201, resp.text
            marketplace_store.STATE_FILE.write_text("{ not json", encoding="utf-8")

            assert marketplace_store.load_state() == {}
            assert marketplace_store._state_corrupt is True
            # Fail closed: the installed skill counts as installed + unapproved.
            assert "hello_market" in marketplace_store.installed_names()
            assert "hello_market" in marketplace_store.unapproved_names()

            created = client.post("/api/auth/users", json={"username": "corruptuser", "password": "test1234", "role": "user"}, headers=headers)
            login = client.post("/api/auth/login", json={"username": "corruptuser", "password": "test1234"})
            user_headers = {"Authorization": f"Bearer {login.json()['token']}"}
            try:
                assert "hello_market" not in _skill_names(client, user_headers)
                assert "hello_market" not in {s["name"] for s in client.get("/api/skills", headers=user_headers).json()}
            finally:
                client.delete(f"/api/auth/users/{created.json()['id']}", headers=headers)
        finally:
            # Uninstall first (state-based), then clear any corrupt state file
            # and fall back to a direct rmtree for the orphaned dir.
            client.delete("/api/marketplace/hello_market", headers=headers)
            marketplace_store.STATE_FILE.unlink(missing_ok=True)
            import shutil as _shutil
            _shutil.rmtree(marketplace_store.MARKETPLACE_DIR / "hello_market", ignore_errors=True)
            assert not (marketplace_store.MARKETPLACE_DIR / "hello_market").exists()


def test_marketplace_entitlement_gate(monkeypatch):
    """Users whose plan lacks skill_marketplace get 403 on the marketplace API."""
    from app.main import app
    from app.routers.auth import get_current_user

    with TestClient(app) as client:
        headers = _owner_headers(client)

        created = client.post(
            "/api/subscriptions/plans",
            json={
                "name": "NoMarket",
                "price_sats": 0,
                "duration_days": 30,
                "entitlements": {"skill_marketplace": False},
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        plan_id = created.json()["id"]
        try:
            sub = client.post("/api/subscriptions/subscribe", json={"plan_id": plan_id}, headers=headers)
            assert sub.status_code == 201, sub.text
            sub_id = sub.json()["subscription_id"]
            try:
                resp = client.get("/api/marketplace", headers=headers)
                assert resp.status_code == 403, resp.text
            finally:
                client.post(f"/api/subscriptions/cancel/{sub_id}", headers=headers)
        finally:
            client.delete(f"/api/subscriptions/plans/{plan_id}", headers=headers)
