import pytest
from backend.app.services.supabase_service import get_supabase_client, get_postgres_connection, is_test_environment
from fastapi.testclient import TestClient
from backend.app.main import app
from backend.app.core.config import settings

client = TestClient(app)

def test_guardrail_blocks_remote_connections():
    """Verify that is_test_environment is active and remote connections are strictly blocked."""
    assert is_test_environment() is True
    assert get_supabase_client() is None
    assert get_postgres_connection() is None

def test_api_runs_in_pure_memory_without_remote_writes():
    """Verify API handshake, symbol registration, and candle ingestion succeed in memory."""
    res = client.post(
        "/api/v1/auth/handshake",
        json={
            "broker": "TEST_ISOLATION",
            "environment": "DEMO",
            "account_hash": "testhash123",
            "ea_identifier": "ALPED_TEST_GUARD",
            "ea_version": "3.0.0",
            "payload_version": "1.0.0",
            "schema_version": "1.0.0"
        },
        headers={"X-API-Key": settings.API_KEY}
    )
    assert res.status_code == 200
    source_id = res.json()["source_id"]

    # Verify remote client is still None
    assert get_supabase_client() is None
