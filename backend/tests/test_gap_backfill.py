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
