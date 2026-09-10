import os
import pytest

# Enforce test environment flags immediately
os.environ["ENV"] = "test"
os.environ["TESTING"] = "true"
os.environ["ENVIRONMENT"] = "test"

from backend.app.core.config import settings
from backend.app.services import supabase_service
from backend.app.repositories.candle_repo import _memory_candles, _memory_gaps
from backend.app.repositories.source_repo import _memory_sources
from backend.app.repositories.account_repo import _memory_snapshots, _memory_positions, _memory_events
from backend.app.repositories.symbol_repo import _memory_symbols
from backend.app.repositories.swing_repo import _memory_swings

DEFAULT_TEST_SOURCE_ID = "663767a265a3d4c29c3cff1dca10fee9966fb053ea8dfd613a3f70a73a9db757"

def _reset_memory_stores():
    _memory_candles.clear()
    _memory_gaps.clear()
    _memory_sources.clear()
    _memory_snapshots.clear()
    _memory_positions.clear()
    _memory_events.clear()
    _memory_symbols.clear()
    _memory_swings.clear()

    # Re-seed default source for tests that expect a pre-existing source
    _memory_sources[DEFAULT_TEST_SOURCE_ID] = {
        "source_id": DEFAULT_TEST_SOURCE_ID,
        "broker": "VALETAX",
        "environment": "DEMO",
        "account_hash": "testhash",
        "ea_identifier": "ALPED_TEST",
        "ea_version": "3.0.0",
        "payload_version": "1.0.0",
        "schema_version": "1.0.0",
        "is_active": True
    }

@pytest.fixture(autouse=True)
def test_environment_guard(monkeypatch):
    """
    Automatic fixture applied to all tests:
    1. Sets explicit environment variables (ENV=test, TESTING=true)
    2. Overrides get_supabase_client() and get_postgres_connection() to strictly return None
    3. Cleanses and re-seeds in-memory stores before and after each test
    """
    settings.ENVIRONMENT = "test"
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("TESTING", "true")
    monkeypatch.setenv("ENVIRONMENT", "test")

    # Hard mock both remote client and postgres connection
    monkeypatch.setattr(supabase_service, "_supabase_client", None)
    monkeypatch.setattr(supabase_service, "get_supabase_client", lambda: None)
    monkeypatch.setattr(supabase_service, "get_postgres_connection", lambda: None)

    _reset_memory_stores()

    yield

    _reset_memory_stores()
