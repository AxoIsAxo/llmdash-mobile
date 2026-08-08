"""P7 — text-to-speech endpoint tests.

Cover: successful synthesis (mocked provider) returns audio/mpeg bytes and
charges the user's token budget (User.token_usage + TokenUsageLog), the token
budget cap 403s once the effective limit would be exceeded, the `tts`
entitlement gate 403s when the user's plan locks TTS, provider failures map
to 502, and the OpenRouter response parser handles both audio payload shapes.

Same isolation pattern as the other test modules: env set at module level,
app imports lazy.
"""

import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="llmdash_tts_test_")
os.environ.setdefault("DATABASE_PATH", os.path.join(_tmp, "test.db"))
os.environ.setdefault("UPLOADS_DIR", os.path.join(_tmp, "uploads"))
os.environ.setdefault("DOCUMENTS_DIR", os.path.join(_tmp, "documents"))
os.environ.setdefault("MEMORY_DIR", os.path.join(_tmp, "memory"))
os.environ.setdefault("JWT_SECRET", "test-secret-tts")

from fastapi.testclient import TestClient  # noqa: E402

FAKE_MP3 = b"\xff\xfb\x90\x00fake-mp3-bytes"


def test_tts_defaults():
    """Defaults must point at a working OpenRouter audio-output model + voice."""
    from app.tts import resolve_default_voice, resolve_model

    assert resolve_model() == "openai/gpt-audio-mini"
    assert resolve_default_voice() == "alloy"


def test_extract_audio_base64_both_shapes():
    """The OpenRouter TTS response parser handles message.audio.data AND
    content-part audio payloads (no HTTP involved)."""
    from app.tts import _extract_audio_base64

    # Primary shape: choices[0].message.audio.data
    assert _extract_audio_base64({
        "choices": [{"message": {"audio": {"data": "bXAz", "format": "mp3"}}}],
    }) == "bXAz"
    # Fallback: content parts with type == "audio"
    assert _extract_audio_base64({
        "choices": [{"message": {"content": [
            {"type": "text", "text": "hi"},
            {"type": "audio", "audio": {"data": "bXAz"}},
        ]}}],
    }) == "bXAz"
    # Garbage -> None (caller raises a safe error)
    assert _extract_audio_base64({}) is None
    assert _extract_audio_base64({"choices": [{"message": {"content": "no audio"}}]}) is None


def _owner_headers(client):
    status = client.get("/api/auth/status").json()
    if status["needs_setup"]:
        client.post("/api/auth/setup", json={"username": "owner", "password": "test1234"})
    login = client.post("/api/auth/login", json={"username": "owner", "password": "test1234"})
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['token']}"}


def test_tts_endpoint(monkeypatch):
    from app.main import app

    with TestClient(app) as client:
        headers = _owner_headers(client)

        created = client.post(
            "/api/auth/users",
            json={"username": "ttsuser", "password": "test1234", "role": "user"},
            headers=headers,
        )
        assert created.status_code in (200, 201), created.text
        user_id = created.json()["id"]
        # Cap the user's token budget via the admin update endpoint.
        assert client.put(f"/api/auth/users/{user_id}", json={"token_limit": 50}, headers=headers).status_code == 200
        login = client.post("/api/auth/login", json={"username": "ttsuser", "password": "test1234"})
        user_headers = {"Authorization": f"Bearer {login.json()['token']}"}
        try:
            # Validation: whitespace-only text -> 400; bad voice -> 400.
            assert client.post("/api/chat/tts", json={"text": "   "}, headers=user_headers).status_code == 400
            assert client.post("/api/chat/tts", json={"text": "hi", "voice": "a b<c>"}, headers=user_headers).status_code == 400

            # Successful synthesis (mocked) -> audio/mpeg + token accounting.
            monkeypatch.setattr("app.main.tts_synthesize", lambda text, voice: FAKE_MP3)
            resp = client.post("/api/chat/tts", json={"text": "hello world"}, headers=user_headers)
            assert resp.status_code == 200, resp.text
            assert resp.headers["content-type"].startswith("audio/mpeg")
            assert resp.content == FAKE_MP3

            me = client.get("/api/auth/me", headers=user_headers).json()
            # est = max(25, 11 // 4) = 25
            assert me["token_usage"] == 25, me["token_usage"]

            # Token budget cap: usage 25 + est 25 = 50 == limit, ok; a longer
            # text (est 50) would exceed the 50-token cap -> 403.
            resp = client.post("/api/chat/tts", json={"text": "x" * 200}, headers=user_headers)
            assert resp.status_code == 403, resp.text

            # Entitlement gate: a plan with tts off -> 403.
            plan = client.post(
                "/api/subscriptions/plans",
                json={"name": "NoTts", "price_sats": 0, "duration_days": 30, "entitlements": {"tts": False}},
                headers=headers,
            )
            assert plan.status_code == 201, plan.text
            plan_id = plan.json()["id"]
            try:
                sub = client.post("/api/subscriptions/subscribe", json={"plan_id": plan_id}, headers=user_headers)
                assert sub.status_code == 201, sub.text
                sub_id = sub.json()["subscription_id"]
                try:
                    resp = client.post("/api/chat/tts", json={"text": "hi"}, headers=user_headers)
                    assert resp.status_code == 403, resp.text
                finally:
                    client.post(f"/api/subscriptions/cancel/{sub_id}", headers=user_headers)
            finally:
                client.delete(f"/api/subscriptions/plans/{plan_id}", headers=headers)

            # Provider failure -> 502 with a safe message.
            monkeypatch.setattr("app.main.tts_synthesize", lambda text, voice: (_ for _ in ()).throw(RuntimeError("TTS error (401): nope")))
            resp = client.post("/api/chat/tts", json={"text": "hi"}, headers=user_headers)
            assert resp.status_code == 502 and "TTS error (401)" in resp.json()["detail"]
        finally:
            client.delete(f"/api/auth/users/{user_id}", headers=headers)
