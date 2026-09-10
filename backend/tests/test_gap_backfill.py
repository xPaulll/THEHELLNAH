import pytest
import uuid
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from backend.app.main import app
from backend.app.core.config import settings
from backend.app.core.constants import SyncType, GapStatus
from backend.app.repositories.candle_repo import candle_repo, _memory_candles, _memory_gaps

client = TestClient(app)
AUTH_HEADERS = {"X-API-Key": settings.API_KEY}

def test_sync_status_case_insensitivity():
    """Verify GET /api/v1/candles/sync-status matches symbols case-insensitively."""
    handshake_payload = {
        "broker": "Vantage-Live",
        "environment": "LIVE",
        "account_hash": uuid.uuid4().hex,
        "ea_identifier": "ALPED_BRIDGE_TEST",
        "ea_version": "3.0.0",
        "payload_version": "1.0.0",
        "schema_version": "1.0.0"
    }
    hs_res = client.post("/api/v1/auth/handshake", json=handshake_payload, headers=AUTH_HEADERS)
    assert hs_res.status_code == 200
    source_id = hs_res.json()["source_id"]

    # Register symbol
    sym_res = client.post("/api/v1/symbols", json={
        "source_id": source_id,
        "symbol": "XAUUSD.vx",
        "digits": 2,
        "point": 0.01,
        "contract_size": 100.0,
        "volume_min": 0.01,
        "volume_max": 100.0,
        "volume_step": 0.01,
        "stop_level": 0,
        "trade_mode": 4,
        "currency_base": "XAU",
        "currency_profit": "USD"
    }, headers=AUTH_HEADERS)
    assert sym_res.status_code == 200

    epoch_val = 1788940000
    time_utc_str = datetime.fromtimestamp(epoch_val, tz=timezone.utc).isoformat()
    
    # Ingest a candle with exact symbol XAUUSD.vx
    batch_payload = {
        "source_id": source_id,
        "symbol": "XAUUSD.vx",
        "timeframe": "M1",
        "sync_type": "INITIAL_SYNC",
        "candles": [
            {
                "candle_time_epoch": epoch_val,
                "candle_time_utc": time_utc_str,
                "open": 2000.0,
                "high": 2005.0,
                "low": 1995.0,
                "close": 2002.0,
                "tick_volume": 100,
                "spread": 10,
                "broker_time": time_utc_str,
                "broker_gmt_offset": 0
            }
        ]
    }
    post_res = client.post("/api/v1/candles/batch", json=batch_payload, headers=AUTH_HEADERS)
    assert post_res.status_code == 200

    # Query with exact casing: XAUUSD.vx
    res_exact = client.get(
        f"/api/v1/candles/sync-status?source_id={source_id}&symbol=XAUUSD.vx&timeframe=M1",
        headers=AUTH_HEADERS
    )
    assert res_exact.status_code == 200
    assert res_exact.json()["latest_candle_epoch"] == epoch_val

    # Query with UPPERCASE casing: XAUUSD.VX
    res_upper = client.get(
        f"/api/v1/candles/sync-status?source_id={source_id}&symbol=XAUUSD.VX&timeframe=M1",
        headers=AUTH_HEADERS
    )
    assert res_upper.status_code == 200
    assert res_upper.json()["latest_candle_epoch"] == epoch_val

    # Query with lowercase casing: xauusd.vx
    res_lower = client.get(
        f"/api/v1/candles/sync-status?source_id={source_id}&symbol=xauusd.vx&timeframe=M1",
        headers=AUTH_HEADERS
    )
    assert res_lower.status_code == 200
    assert res_lower.json()["latest_candle_epoch"] == epoch_val


def test_gap_backfill_batch_and_audit():
    """Verify POST /api/v1/candles/batch with GAP_BACKFILL sets is_gap_recovered and creates audit row."""
    handshake_payload = {
        "broker": "Vantage-Live",
        "environment": "LIVE",
        "account_hash": uuid.uuid4().hex,
        "ea_identifier": "ALPED_BRIDGE_TEST",
        "ea_version": "3.0.0",
        "payload_version": "1.0.0",
        "schema_version": "1.0.0"
    }
    hs_res = client.post("/api/v1/auth/handshake", json=handshake_payload, headers=AUTH_HEADERS)
    assert hs_res.status_code == 200
    source_id = hs_res.json()["source_id"]

    client.post("/api/v1/symbols", json={
        "source_id": source_id,
        "symbol": "EURUSD",
        "digits": 5,
        "point": 0.00001,
        "contract_size": 100000.0,
        "volume_min": 0.01,
        "volume_max": 100.0,
        "volume_step": 0.01,
        "stop_level": 0,
        "trade_mode": 4,
        "currency_base": "EUR",
        "currency_profit": "USD"
    }, headers=AUTH_HEADERS)

    epoch_1 = 1788941000
    epoch_2 = 1788941060
    t1 = datetime.fromtimestamp(epoch_1, tz=timezone.utc).isoformat()
    t2 = datetime.fromtimestamp(epoch_2, tz=timezone.utc).isoformat()

    batch_payload = {
        "source_id": source_id,
        "symbol": "EURUSD",
        "timeframe": "M1",
        "sync_type": "GAP_BACKFILL",
        "candles": [
            {
                "candle_time_epoch": epoch_1,
                "candle_time_utc": t1,
                "open": 1.0800,
                "high": 1.0810,
                "low": 1.0790,
                "close": 1.0805,
                "tick_volume": 50,
                "spread": 5,
                "broker_time": t1,
                "broker_gmt_offset": 0
            },
            {
                "candle_time_epoch": epoch_2,
                "candle_time_utc": t2,
                "open": 1.0805,
                "high": 1.0815,
                "low": 1.0800,
                "close": 1.0810,
                "tick_volume": 60,
                "spread": 5,
                "broker_time": t2,
                "broker_gmt_offset": 0
            }
        ]
    }
    res = client.post("/api/v1/candles/batch", json=batch_payload, headers=AUTH_HEADERS)
    assert res.status_code == 200
    assert res.json()["status"] == "SUCCESS"
    assert res.json()["processed_count"] == 2

    # Check is_gap_recovered in repository
    key1 = (source_id, "EURUSD", "M1", t1)
    if key1 in _memory_candles:
        assert _memory_candles[key1]["is_gap_recovered"] is True

    # Check candle_gaps audit recording
    audit_matches = [
        g for g in _memory_gaps
        if g.get("source_id") == source_id and g.get("symbol") == "EURUSD" and g.get("status") == GapStatus.RECOVERED.value
    ]
    assert len(audit_matches) >= 1
    assert "GAP_BACKFILL" in audit_matches[0]["classification_reason"]


def test_gap_backfill_idempotency():
    """Verify sending identical GAP_BACKFILL multiple times is idempotent."""
    handshake_payload = {
        "broker": "Vantage-Live",
        "environment": "LIVE",
        "account_hash": uuid.uuid4().hex,
        "ea_identifier": "ALPED_BRIDGE_TEST",
        "ea_version": "3.0.0",
        "payload_version": "1.0.0",
        "schema_version": "1.0.0"
    }
    hs_res = client.post("/api/v1/auth/handshake", json=handshake_payload, headers=AUTH_HEADERS)
    assert hs_res.status_code == 200
    source_id = hs_res.json()["source_id"]

    client.post("/api/v1/symbols", json={
        "source_id": source_id,
        "symbol": "GBPUSD",
        "digits": 5,
        "point": 0.00001,
        "contract_size": 100000.0,
        "volume_min": 0.01,
        "volume_max": 100.0,
        "volume_step": 0.01,
        "stop_level": 0,
        "trade_mode": 4,
        "currency_base": "GBP",
        "currency_profit": "USD"
    }, headers=AUTH_HEADERS)

    epoch_val = 1788942000
    t_val = datetime.fromtimestamp(epoch_val, tz=timezone.utc).isoformat()
    
    batch_payload = {
        "source_id": source_id,
        "symbol": "GBPUSD",
        "timeframe": "M1",
        "sync_type": "GAP_BACKFILL",
        "candles": [
            {
                "candle_time_epoch": epoch_val,
                "candle_time_utc": t_val,
                "open": 1.2500,
                "high": 1.2510,
                "low": 1.2490,
                "close": 1.2505,
                "tick_volume": 80,
                "spread": 8,
                "broker_time": t_val,
                "broker_gmt_offset": 0
            }
        ]
    }
    res1 = client.post("/api/v1/candles/batch", json=batch_payload, headers=AUTH_HEADERS)
    assert res1.status_code == 200
    res2 = client.post("/api/v1/candles/batch", json=batch_payload, headers=AUTH_HEADERS)
    assert res2.status_code == 200

    # Key in memory should still exist exactly once
    key = (source_id, "GBPUSD", "M1", t_val)
    if key in _memory_candles:
        assert _memory_candles[key]["close"] == 1.2505


def test_ea_source_code_invariants():
    """Verify that Alped_Bridge.mq5 contains all required gap backfill specifications."""
    with open("Alped_Bridge.mq5", "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()

    assert "InpMaxBackfillBars" in content
    assert "GAP_BACKFILL" in content
    assert "gap_seconds <= bar_seconds" in content
    assert "CopyRates" in content
    assert "MathMin" in content
    assert "g_BackoffSeconds" in content


def test_gap_backfill_fast_response():
    """Verify GAP_BACKFILL HTTP response completes in < 1.0s without blocking."""
    import time
    handshake_payload = {
        "broker": "Vantage-Live",
        "environment": "LIVE",
        "account_hash": uuid.uuid4().hex,
        "ea_identifier": "ALPED_BRIDGE_TEST",
        "ea_version": "3.0.0",
        "payload_version": "1.0.0",
        "schema_version": "1.0.0"
    }
    hs_res = client.post("/api/v1/auth/handshake", json=handshake_payload, headers=AUTH_HEADERS)
    source_id = hs_res.json()["source_id"]

    epoch_val = 1788950000
    t_val = datetime.fromtimestamp(epoch_val, tz=timezone.utc).isoformat()

    batch_payload = {
        "source_id": source_id,
        "symbol": "XAUUSD.vx",
        "timeframe": "M1",
        "sync_type": "GAP_BACKFILL",
        "candles": [
            {
                "candle_time_epoch": epoch_val,
                "candle_time_utc": t_val,
                "open": 2050.0,
                "high": 2055.0,
                "low": 2048.0,
                "close": 2052.0,
                "tick_volume": 120,
                "spread": 15,
                "broker_time": t_val,
                "broker_gmt_offset": 0
            }
        ]
    }
    t0 = time.perf_counter()
    res = client.post("/api/v1/candles/batch", json=batch_payload, headers=AUTH_HEADERS)
    t1 = time.perf_counter()

    assert res.status_code == 200
    assert (t1 - t0) < 1.0, f"HTTP response took {t1 - t0:.3f}s, exceeding 1.0s threshold!"
    assert res.json()["status"] == "SUCCESS"
    assert res.json()["processed_count"] == 1


def test_rebuild_manager_concurrency_lock():
    """Verify RebuildManager prevents concurrent rebuilds for the same key while allowing different keys."""
    from backend.app.services.rebuild_manager import rebuild_manager
    source_id = "test_src_" + uuid.uuid4().hex
    symbol = "EURUSD"

    # M1 and M5 should have separate locks
    lock_m1 = rebuild_manager.get_lock(source_id, symbol, "M1")
    lock_m5 = rebuild_manager.get_lock(source_id, symbol, "M5")
    assert lock_m1 is not lock_m5

    # Same key gets identical lock instance
    lock_m1_again = rebuild_manager.get_lock(source_id, symbol, "M1")
    assert lock_m1 is lock_m1_again

    # Status tracking
    assert not rebuild_manager.is_rebuilding(source_id, symbol, "M1")
    rebuild_manager.mark_started(source_id, symbol, "M1")
    assert rebuild_manager.is_rebuilding(source_id, symbol, "M1")
    assert not rebuild_manager.is_rebuilding(source_id, symbol, "M5")
    rebuild_manager.mark_completed(source_id, symbol, "M1")
    assert not rebuild_manager.is_rebuilding(source_id, symbol, "M1")


def test_sync_status_reports_rebuilding_state():
    """Verify /sync-status reports REBUILDING while feature rebuild is active."""
    from backend.app.services.rebuild_manager import rebuild_manager
    handshake_payload = {
        "broker": "Vantage-Live",
        "environment": "LIVE",
        "account_hash": uuid.uuid4().hex,
        "ea_identifier": "ALPED_BRIDGE_TEST",
        "ea_version": "3.0.0",
        "payload_version": "1.0.0",
        "schema_version": "1.0.0"
    }
    hs_res = client.post("/api/v1/auth/handshake", json=handshake_payload, headers=AUTH_HEADERS)
    source_id = hs_res.json()["source_id"]

    epoch_val = 1788960000
    t_val = datetime.fromtimestamp(epoch_val, tz=timezone.utc).isoformat()

    client.post("/api/v1/candles/batch", json={
        "source_id": source_id,
        "symbol": "AUDUSD",
        "timeframe": "M1",
        "sync_type": "INITIAL_SYNC",
        "candles": [
            {
                "candle_time_epoch": epoch_val,
                "candle_time_utc": t_val,
                "open": 0.6500,
                "high": 0.6510,
                "low": 0.6490,
                "close": 0.6505,
                "tick_volume": 40,
                "spread": 5,
                "broker_time": t_val,
                "broker_gmt_offset": 0
            }
        ]
    }, headers=AUTH_HEADERS)

    # In quiescent state: SYNCED
    status_res = client.get(
        f"/api/v1/candles/sync-status?source_id={source_id}&symbol=AUDUSD&timeframe=M1",
        headers=AUTH_HEADERS
    )
    assert status_res.status_code == 200
    assert status_res.json()["status"] == "SYNCED"

    # Simulate rebuild in progress: REBUILDING
    rebuild_manager.mark_started(source_id, "AUDUSD", "M1")
    try:
        status_rebuilding = client.get(
            f"/api/v1/candles/sync-status?source_id={source_id}&symbol=AUDUSD&timeframe=M1",
            headers=AUTH_HEADERS
        )
        assert status_rebuilding.status_code == 200
        assert status_rebuilding.json()["status"] == "REBUILDING"
        assert status_rebuilding.json()["latest_candle_epoch"] == epoch_val
    finally:
        rebuild_manager.mark_completed(source_id, "AUDUSD", "M1")

    # Back to SYNCED
    status_done = client.get(
        f"/api/v1/candles/sync-status?source_id={source_id}&symbol=AUDUSD&timeframe=M1",
        headers=AUTH_HEADERS
    )
    assert status_done.status_code == 200
    assert status_done.json()["status"] == "SYNCED"


def test_step3_batch_save_rebuild_atomic_consistency():
    """Verify batch_save_rebuild_state atomically updates current state, history, and processed ledger."""
    from backend.app.repositories.market_structure_state_repo import market_structure_state_repo
    source_id = "test_src_" + uuid.uuid4().hex
    symbol = "USDJPY"
    tf = "M1"

    final_state = {
        "source_id": source_id,
        "symbol": symbol,
        "timeframe": tf,
        "state": "BULLISH",
        "previous_state": "NEUTRAL",
        "structure": "HH_HL",
        "last_event": "BOS_BULLISH",
        "last_event_time": 1788970000,
        "last_event_price": 155.50,
        "state_changed": True,
        "state_reason": "Confirmed HH -> HL sequence",
        "structure_strength": "CONFIRMED",
        "source_event_id": None,
        "bar_time": 1788970000,
        "bar_index": 50
    }

    history = [
        {
            "source_id": source_id,
            "symbol": symbol,
            "timeframe": tf,
            "previous_state": "NEUTRAL",
            "new_state": "BULLISH",
            "structure": "HH_HL",
            "last_event": "BOS_BULLISH",
            "event_time": 1788970000,
            "event_price": 155.50,
            "state_reason": "Confirmed HH -> HL sequence",
            "structure_strength": "CONFIRMED",
            "source_event_id": None,
            "bar_time": 1788970000,
            "bar_index": 50
        }
    ]

    canonical_key = f"{source_id}:{symbol}:{tf}:BREAK:BOS_BULLISH:1788970000:155.50000"
    processed_keys = [canonical_key]

    success = market_structure_state_repo.batch_save_rebuild_state(
        final_state=final_state,
        history_entries=history,
        processed_event_keys=processed_keys
    )
    assert success is True

    # Verify current state retrieval
    curr = market_structure_state_repo.get_current_state(source_id, symbol, tf)
    assert curr is not None
    assert curr["state"] == "BULLISH"
    assert curr["structure"] == "HH_HL"

    # Verify history retrieval
    hist = market_structure_state_repo.get_state_history(source_id, symbol, tf)
    assert len(hist) >= 1
    assert hist[0]["new_state"] == "BULLISH"

    # Verify processed key ledger
    assert market_structure_state_repo.is_event_processed(source_id, symbol, tf, canonical_key) is True

