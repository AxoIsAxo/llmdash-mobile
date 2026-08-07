"""Extrovert OIDC login tests — drive the real auth router against a mock
Extrovert provider (discovery + JWKS + token + userinfo) with a genuine
RSA-signed ID token. Covers: new signup and converting an existing password
account by matching username.
"""

import base64
import json
import os
import re
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

_tmp = tempfile.mkdtemp(prefix="llmdash_extrovert_test_")
os.environ["DATABASE_PATH"] = os.path.join(_tmp, "test.db")
os.environ["UPLOADS_DIR"] = os.path.join(_tmp, "uploads")
os.environ["DOCUMENTS_DIR"] = os.path.join(_tmp, "documents")
os.environ["JWT_SECRET"] = "test-secret-extrovert"
os.environ["MEMORY_DIR"] = os.path.join(_tmp, "memory")

import jwt as pyjwt  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import config as app_config  # noqa: E402
from app.main import app  # noqa: E402

# --- mock Extrovert provider ------------------------------------------------

_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PRIVATE_PEM = _PRIVATE_KEY.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
)
_PUB = _PRIVATE_KEY.public_key().public_numbers()


def _b64u_int(i: int) -> str:
    return base64.urlsafe_b64encode(i.to_bytes((i.bit_length() + 7) // 8, "big")).rstrip(b"=").decode()


class Handler(BaseHTTPRequestHandler):
    id_token = ""
    userinfo = {"sub": "sub-123", "preferred_username": "axo"}
    token_body: dict = {}

    def do_GET(self):
        if self.path == "/.well-known/openid-configuration":
            doc = {
                "issuer": f"http://127.0.0.1:{self.server.server_port}",
                "authorization_endpoint": f"http://127.0.0.1:{self.server.server_port}/authorize",
                "token_endpoint": f"http://127.0.0.1:{self.server.server_port}/token",
                "userinfo_endpoint": f"http://127.0.0.1:{self.server.server_port}/userinfo",
                "jwks_uri": f"http://127.0.0.1:{self.server.server_port}/jwks",
                "id_token_signing_alg_values_supported": ["RS256"],
            }
            self._send(doc)
        elif self.path == "/jwks":
            self._send({
                "keys": [{
                    "kty": "RSA", "kid": "k1", "use": "sig", "alg": "RS256",
                    "n": _b64u_int(_PUB.n), "e": _b64u_int(_PUB.e),
                }],
            })
        elif self.path == "/userinfo":
            self._send(Handler.userinfo)
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/token":
            Handler.token_body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            self._send({"access_token": "acc", "token_type": "Bearer", "id_token": Handler.id_token})
        else:
            self.send_error(404)

    def _send(self, obj):
        data = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass



def _start_provider():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def _enable_extrovert(monkeypatch, issuer):
    monkeypatch.setattr(app_config.settings, "extrovert_client_id", "llmdash-client")
    monkeypatch.setattr(app_config.settings, "extrovert_client_secret", "super-secret")
    monkeypatch.setattr(app_config.settings, "extrovert_issuer", issuer)
    monkeypatch.setattr(app_config.settings, "extrovert_allow_signup", True)
    monkeypatch.setattr(app_config.settings, "ip_account_limit", 100)  # many registrations share 127.0.0.1
    monkeypatch.setattr(app_config.settings, "extrovert_redirect_uri", "http://testserver/api/auth/extrovert/callback")


def _id_token(issuer, nonce, sub="sub-123", username="axo") -> str:
    now = int(time.time())
    payload = {
        "iss": issuer, "sub": sub, "aud": "llmdash-client",
        "exp": now + 600, "iat": now, "nonce": nonce,
        "preferred_username": username,
    }
    return pyjwt.encode(payload, _PRIVATE_PEM, algorithm="RS256", headers={"kid": "k1"})


def _owner_headers(client):
    st = client.get("/api/auth/status").json()
    if st["needs_setup"]:
        client.post("/api/auth/setup", json={"username": "owner", "password": "test1234"})
    tok = client.post("/api/auth/login", json={"username": "owner", "password": "test1234"}).json()["token"]
    return {"Authorization": f"Bearer {tok}"}


def _run_oauth(client, issuer, sub="sub-123", username="axo"):
    start = client.get("/api/auth/extrovert/start")
    assert start.status_code == 200
    url = start.json()["url"]
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    state, nonce = q["state"][0], q["nonce"][0]
    assert q["code_challenge_method"] == ["S256"]
    Handler.id_token = _id_token(issuer, nonce, sub=sub, username=username)
    Handler.userinfo = {"sub": sub, "preferred_username": username}
    resp = client.get(f"/api/auth/extrovert/callback?state={state}&code=abc")
    assert resp.status_code == 200
    m = re.search(r"llmdash_token', \"([^\"]+)\"\)", resp.text)
    assert m, resp.text[:400]
    return m.group(1)


def test_extrovert_login_creates_new_account(monkeypatch):
    srv, issuer = _start_provider()
    _enable_extrovert(monkeypatch, issuer)
    monkeypatch.setattr(app_config.settings, "registration_enabled", True)
    with TestClient(app) as client:
        _owner_headers(client)  # create the owner account first
        token = _run_oauth(client, issuer, sub="sub-new", username="newperson")
        me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200
        assert me.json()["username"] == "@newperson"  # namespaced
        assert me.json()["role"] == "user"
    srv.shutdown()


def test_no_username_matching_and_creation_is_namespaced(monkeypatch):
    """Extrovert usernames are @-namespaced and NEVER match normal accounts:
    a normal 'axo' account stays untouched when an Extrovert 'axo' signs up —
    both can exist ('axo' and '@axo')."""
    srv, issuer = _start_provider()
    _enable_extrovert(monkeypatch, issuer)
    monkeypatch.setattr(app_config.settings, "registration_enabled", True)
    with TestClient(app) as client:
        headers = _owner_headers(client)
        reg = client.post("/api/auth/register", json={"username": "axo", "password": "pw12345678"}, headers=headers)
        assert reg.status_code == 200, reg.text

        # Extrovert user with the SAME username signs up -> new @axo account
        token = _run_oauth(client, issuer, sub="sub-123", username="axo")
        me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200
        assert me.json()["username"] == "@axo"  # namespaced, not the normal account
        assert me.json()["extrovert_linked"] is True

        users = client.get("/api/auth/users", headers=headers).json()
        names = sorted(u["username"] for u in users)
        assert "axo" in names and "@axo" in names  # both exist, distinct
    srv.shutdown()


def test_extrovert_login_links_existing_account(monkeypatch):
    """The explicit conversion path: an authenticated user starts mode=link,
    the callback links THAT account (oauth_sub set) and renames it to the
    @-namespaced Extrovert username. No username matching involved."""
    srv, issuer = _start_provider()
    _enable_extrovert(monkeypatch, issuer)
    monkeypatch.setattr(app_config.settings, "registration_enabled", True)
    with TestClient(app) as client:
        headers = _owner_headers(client)
        reg = client.post("/api/auth/register", json={"username": "existing", "password": "pw12345678"}, headers=headers)
        assert reg.status_code == 200, reg.text
        user_headers = {"Authorization": f"Bearer {reg.json()['token']}"}
        before = client.get("/api/auth/users", headers=headers).json()
        existing_id = next(u["id"] for u in before if u["username"] == "existing")

        # start in link mode AS the existing user (their own token)
        start = client.get("/api/auth/extrovert/start?mode=link", headers=user_headers)
        assert start.status_code == 200
        url = start.json()["url"]
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        state, nonce = q["state"][0], q["nonce"][0]
        Handler.id_token = _id_token(issuer, nonce, sub="sub-link-99", username="newname")
        Handler.userinfo = {"sub": "sub-link-99", "preferred_username": "newname"}
        resp = client.get(f"/api/auth/extrovert/callback?state={state}&code=abc")
        assert resp.status_code == 200
        assert "Signed in via Extrovert" in resp.text

        # the SAME account was linked and renamed
        after = client.get("/api/auth/users", headers=headers).json()
        linked = next(u for u in after if u["id"] == existing_id)
        assert linked["username"] == "@newname"

        # and Extrovert login now hits that account (by sub, not username)
        token = _run_oauth(client, issuer, sub="sub-link-99", username="newname")
        me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.json()["user_id"] == existing_id
    srv.shutdown()


def test_extrovert_link_requires_auth(monkeypatch):
    srv, issuer = _start_provider()
    _enable_extrovert(monkeypatch, issuer)
    with TestClient(app) as client:
        assert client.get("/api/auth/extrovert/start?mode=link").status_code == 401
    srv.shutdown()


def test_extrovert_status_flag(monkeypatch):
    srv, issuer = _start_provider()
    with TestClient(app) as client:
        assert client.get("/api/auth/status").json()["extrovert_enabled"] is False
    _enable_extrovert(monkeypatch, issuer)
    with TestClient(app) as client:
        assert client.get("/api/auth/status").json()["extrovert_enabled"] is True
        assert client.get("/api/auth/extrovert/start").status_code == 200
    srv.shutdown()


def test_extrovert_login_works_without_client_secret(monkeypatch):
    """Public-client flow: no client_secret configured -> the token request
    omits it entirely, and PKCE still secures the exchange."""
    srv, issuer = _start_provider()
    _enable_extrovert(monkeypatch, issuer)
    monkeypatch.setattr(app_config.settings, "extrovert_client_secret", "")
    with TestClient(app) as client:
        token = _run_oauth(client, issuer, sub="sub-pub", username="pubperson")
        me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200
        assert me.json()["username"] == "@pubperson"  # namespaced
    assert "client_secret" not in Handler.token_body, Handler.token_body
    srv.shutdown()


def _link_flow(client, issuer, sub, username):
    """Run the authenticated link flow; returns nothing (raises on error)."""
    start = client.get("/api/auth/extrovert/start?mode=link", headers=_LINK_HEADERS)
    assert start.status_code == 200, start.text
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(start.json()["url"]).query)
    Handler.id_token = _id_token(issuer, q["nonce"][0], sub=sub, username=username)
    Handler.userinfo = {"sub": sub, "preferred_username": username}
    resp = client.get(f"/api/auth/extrovert/callback?state={q['state'][0]}&code=abc")
    assert resp.status_code == 200
    return resp


_LINK_HEADERS: dict = {}


def test_extrovert_relink_replaces_previous_identity(monkeypatch):
    """An already-linked account can CHANGE its Extrovert identity: the old
    sub no longer maps to it, the new one does (same user id)."""
    global _LINK_HEADERS
    srv, issuer = _start_provider()
    _enable_extrovert(monkeypatch, issuer)
    monkeypatch.setattr(app_config.settings, "registration_enabled", True)
    with TestClient(app) as client:
        headers = _owner_headers(client)
        reg = client.post("/api/auth/register", json={"username": "flip", "password": "pw12345678"}, headers=headers)
        assert reg.status_code == 200, reg.text
        _LINK_HEADERS = {"Authorization": f"Bearer {reg.json()['token']}"}
        users = client.get("/api/auth/users", headers=headers).json()
        flip_id = next(u["id"] for u in users if u["username"] == "flip")

        # link identity A
        resp = _link_flow(client, issuer, "sub-a", "flipname")
        assert "Signed in via Extrovert" in resp.text
        # change to identity B
        resp = _link_flow(client, issuer, "sub-b", "othername")
        assert "Signed in via Extrovert" in resp.text

        after = client.get("/api/auth/users", headers=headers).json()
        linked = next(u for u in after if u["id"] == flip_id)
        assert linked["username"] == "@othername"  # renamed to the new identity

        # old identity no longer maps to this account -> a NEW account is created
        token_new = _run_oauth(client, issuer, sub="sub-a", username="flipname")
        me_new = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token_new}"})
        assert me_new.json()["user_id"] != flip_id

        # new identity logs into the ORIGINAL account
        token_orig = _run_oauth(client, issuer, sub="sub-b", username="othername")
        me_orig = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token_orig}"})
        assert me_orig.json()["user_id"] == flip_id
    srv.shutdown()


def test_extrovert_relink_rejects_identity_owned_by_another(monkeypatch):
    """Linking an Extrovert identity that already belongs to a DIFFERENT
    account must fail — never hijack or log into the other account."""
    global _LINK_HEADERS
    srv, issuer = _start_provider()
    _enable_extrovert(monkeypatch, issuer)
    monkeypatch.setattr(app_config.settings, "registration_enabled", True)
    with TestClient(app) as client:
        headers = _owner_headers(client)
        # user one links identity X
        reg1 = client.post("/api/auth/register", json={"username": "first", "password": "pw12345678"}, headers=headers)
        _LINK_HEADERS = {"Authorization": f"Bearer {reg1.json()['token']}"}
        resp = _link_flow(client, issuer, "sub-x", "xname")
        assert "Signed in via Extrovert" in resp.text

        # user two tries to link the SAME identity X
        reg2 = client.post("/api/auth/register", json={"username": "second", "password": "pw12345678"}, headers=headers)
        _LINK_HEADERS = {"Authorization": f"Bearer {reg2.json()['token']}"}
        resp = _link_flow(client, issuer, "sub-x", "xname")
        assert "already linked to another LLMDash account" in resp.text

        # the other account was untouched
        users = client.get("/api/auth/users", headers=headers).json()
        second = next(u for u in users if u["username"] == "second")
        assert second["username"] == "second"
    srv.shutdown()
