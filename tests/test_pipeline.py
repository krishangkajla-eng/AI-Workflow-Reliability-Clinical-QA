import asyncio
import time

import pytest
from fastapi.testclient import TestClient

import app as service


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(service, "ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setattr(service, "WEBHOOK_TOKEN", "test-webhook-token")
    monkeypatch.setattr(service, "MAX_ATTEMPTS", 2)
    monkeypatch.setattr(service, "POLL_SECONDS", 0.01)
    monkeypatch.setattr(service, "retry_delay", lambda attempt, retry_after=None: 0)
    monkeypatch.setattr(service, "OUTAGE_MODE", False)
    with TestClient(service.app) as test_client:
        yield test_client


def payload(email="qa-demo@example.com", source_ref="test-001"):
    return {"email": email, "full_name": "Alex Demo", "company": "Example Agency", "source_ref": source_ref}


def wait_for_status(client, key, expected, timeout=2):
    deadline = time.time() + timeout
    headers = {"X-Admin-Token": "test-admin-token"}
    while time.time() < deadline:
        jobs = client.get("/admin/jobs", headers=headers).json()
        match = next((job for job in jobs if job["idem_key"] == key), None)
        if match and match["status"] == expected:
            return match
        time.sleep(0.01)
    raise AssertionError(f"Job did not reach {expected}")


def test_webhook_requires_token(client):
    assert client.post("/webhook/lead", json=payload()).status_code == 401


def test_invalid_payload_rejected(client):
    response = client.post("/webhook/lead", json={"email": "invalid", "full_name": "A"},
                           headers={"X-Webhook-Token": "test-webhook-token"})
    assert response.status_code == 422


def test_duplicate_submission_is_idempotent(client, monkeypatch):
    async def hold_sink(_record):
        await asyncio.sleep(0.15)
    monkeypatch.setattr(service, "downstream_send", hold_sink)
    headers = {"X-Webhook-Token": "test-webhook-token"}
    first = client.post("/webhook/lead", json=payload(), headers=headers)
    second = client.post("/webhook/lead", json=payload(), headers=headers)
    assert first.status_code == 200
    assert first.json()["status"] == "queued"
    assert second.json()["status"] == "duplicate_ignored"
    assert first.json()["idempotency_key"] == second.json()["idempotency_key"]


def test_admin_requires_token(client):
    assert client.get("/admin/jobs").status_code == 401


def test_retry_exhaustion_and_manual_requeue(client, monkeypatch):
    async def failing_sink(_record):
        raise OSError("synthetic downstream outage")
    async def successful_sink(_record):
        return None
    monkeypatch.setattr(service, "downstream_send", failing_sink)
    response = client.post("/webhook/lead", json=payload(email="outage@example.com", source_ref="outage-001"),
                           headers={"X-Webhook-Token": "test-webhook-token"})
    key = response.json()["idempotency_key"]
    failed = wait_for_status(client, key, "FAILED")
    assert failed["attempts"] == 2
    admin = {"X-Admin-Token": "test-admin-token"}
    requeued = client.post("/admin/requeue-failed", headers=admin)
    assert requeued.status_code == 200
    assert requeued.json()["requeued_count"] == 1
    monkeypatch.setattr(service, "downstream_send", successful_sink)
    assert wait_for_status(client, key, "PROCESSED")["status"] == "PROCESSED"


def test_wrong_tokens_rejected(client):
    bad_webhook = client.post("/webhook/lead", json=payload(), headers={"X-Webhook-Token": "wrong"})
    bad_admin = client.get("/admin/jobs", headers={"X-Admin-Token": "wrong"})
    assert bad_webhook.status_code == 401
    assert bad_admin.status_code == 401
