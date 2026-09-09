import pytest
import time
from fastapi.testclient import TestClient
from backend.app.main import app
from backend.app.services.live_market_manager import LiveMarketManager
from backend.app.models.live_market import LiveMarketPayload, TickData, Bar0Data
from backend.app.services.websocket_manager import WebSocketManager
from backend.app.repositories.source_repo import source_repo, _memory_sources

client = TestClient(app)

TEST_SOURCE_ID = "663767a265a3d4c29c3cff1dca10fee9966fb053ea8dfd613a3f70a73a9db757"

# Set directly in memory to avoid remote cloud Supabase network latency in tests
_memory_sources[TEST_SOURCE_ID] = {
    "source_id": TEST_SOURCE_ID,
    "broker": "VALETAX",
    "environment": "DEMO",
    "account_hash": "testhash",
    "ea_identifier": "ALPED_TEST",
    "ea_version": "3.0.0",
    "payload_version": "1.0.0",
    "schema_version": "1.0.0",
    "is_active": True
}

def make_sample_payload(bid=4380.50, ask=4380.80, spread=30, close_m1=4380.50, vol_m1=10):
    return LiveMarketPayload(
        source_id=TEST_SOURCE_ID,
        symbol="XAUUSD.vx",
        tick=TickData(
            bid=bid,
            ask=ask,
            spread=spread,
            tick_time_utc="2026-09-09T04:50:00Z",
            tick_time_epoch=1788929400
        ),
        timeframes={
            "M1": Bar0Data(
                timeframe="M1",
                time_epoch=1788929400,
                time_utc="2026-09-09T04:50:00Z",
                open=4380.00,
                high=4381.00,
                low=4379.50,
                close=close_m1,
                volume=vol_m1
            ),
            "M5": Bar0Data(
                timeframe="M5",
                time_epoch=1788929100,
                time_utc="2026-09-09T04:45:00Z",
                open=4378.00,
                high=4381.00,
                low=4377.50,
                close=close_m1,
                volume=120
            )
        }
    )

def test_live_market_manager_ingest_and_deduplication():
    mgr = LiveMarketManager()
    p1 = make_sample_payload(bid=4380.50)
    
    # First ingestion -> changed = True
    changed1 = mgr.update_live_market(p1)
    assert changed1 is True

    snap = mgr.get_snapshot()
    assert snap["symbol"] == "XAUUSD.vx"
    assert snap["tick"]["bid"] == 4380.50
    assert snap["timeframes"]["M1"]["close"] == 4380.50
    assert snap["timeframes"]["M5"]["open"] == 4378.00

    # Identical ingestion -> changed = False (Deduplication)
    p2 = make_sample_payload(bid=4380.50)
    changed2 = mgr.update_live_market(p2)
    assert changed2 is False

    # New tick price -> changed = True
    p3 = make_sample_payload(bid=4381.25, close_m1=4381.25)
    changed3 = mgr.update_live_market(p3)
    assert changed3 is True
    assert mgr.get_snapshot()["tick"]["bid"] == 4381.25

def test_stale_data_detection():
    mgr = LiveMarketManager()
    
    # When no update received -> OFFLINE
    status, age = mgr.get_market_liveness()
    assert status == "OFFLINE"

    # Fresh update -> LIVE
    p = make_sample_payload()
    mgr.update_live_market(p)
    status, age = mgr.get_market_liveness()
    assert status == "LIVE"
    assert age < 1.0

    # Simulate 4.5s elapsed -> STALE
    mgr._last_update_mono = time.monotonic() - 4.5
    status, age = mgr.get_market_liveness()
    assert status == "STALE"
    assert 4.0 <= age <= 6.0

    # Simulate 12s elapsed -> OFFLINE
    mgr._last_update_mono = time.monotonic() - 12.0
    status, age = mgr.get_market_liveness()
    assert status == "OFFLINE"
    assert age >= 11.0

def test_post_live_bar_endpoint(monkeypatch):
    # Mock source check to avoid database query in tests
    monkeypatch.setattr(source_repo, "get_source", lambda sid: _memory_sources.get(sid))
    monkeypatch.setattr(source_repo, "update_heartbeat", lambda sid: None)

    payload = make_sample_payload()
    headers = {
        "X-API-Key": "alped_secret_key_v3_secure",
        "Content-Type": "application/json"
    }
    
    resp = client.post("/api/v1/market/live-bar", json=payload.model_dump(), headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "SUCCESS"
    assert data["symbol"] == "XAUUSD.vx"
    assert "is_changed" in data

    # Verify REST snapshot endpoint
    snap_resp = client.get("/api/v1/market/live-state")
    assert snap_resp.status_code == 200
    snap_data = snap_resp.json()
    assert snap_data["symbol"] == "XAUUSD.vx"
    assert snap_data["tick"]["spread"] == 30

def test_post_live_bar_unregistered_source(monkeypatch):
    monkeypatch.setattr(source_repo, "get_source", lambda sid: None)
    payload = make_sample_payload()
    payload.source_id = "0000000000000000000000000000000000000000000000000000000000000000"
    headers = {
        "X-API-Key": "alped_secret_key_v3_secure",
        "Content-Type": "application/json"
    }
    resp = client.post("/api/v1/market/live-bar", json=payload.model_dump(), headers=headers)
    assert resp.status_code == 404

def test_websocket_ping_pong():
    with client.websocket_connect("/ws/live") as ws:
        # Initial message is INIT
        init_msg = ws.receive_json()
        assert init_msg["type"] == "INIT"
        assert "data" in init_msg

        # Send PING
        client_ts = "2026-09-09T04:52:00.123Z"
        ws.send_json({"type": "PING", "client_time": client_ts})

        # Expect PONG with matching client_time
        pong_msg = ws.receive_json()
        assert pong_msg["type"] == "PONG"
        assert pong_msg["client_time"] == client_ts
        assert "server_time" in pong_msg
