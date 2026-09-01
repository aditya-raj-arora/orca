"""Smoke test — keeps CI green from Day 1 (an empty test suite is a red flag,
not a pass). Owner: P1. Expand real coverage per-module as agents land."""
from fastapi.testclient import TestClient

from app.main import app


def test_healthz() -> None:
    client = TestClient(app)
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
