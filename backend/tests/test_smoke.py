"""Smoke tests for the LLMDash backend.

Run with:  pip install -r requirements-dev.txt && pytest
"""

import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="llmdash_test_")
os.environ["DATABASE_PATH"] = os.path.join(_tmp, "test.db")
os.environ["UPLOADS_DIR"] = os.path.join(_tmp, "uploads")
os.environ["DOCUMENTS_DIR"] = os.path.join(_tmp, "documents")
os.environ["JWT_SECRET"] = "test-secret-for-smoke-tests"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def test_full_flow():
    with TestClient(app) as client:
        # --- auth status / setup / login ---
        status = client.get("/api/auth/status")
        assert status.status_code == 200
        assert status.json()["needs_setup"] is True

        setup = client.post("/api/auth/setup", json={"username": "owner", "password": "test1234"})
        assert setup.status_code == 200
        token = setup.json()["token"]
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
