from fastapi.testclient import TestClient
from backend.app.main import app
from backend.app.core.config import settings

client = TestClient(app)
AUTH_HEADERS = {"X-API-Key": settings.API_KEY}

def test_full_pipeline_flow():
    # 1. Handshake
    import uuid
    unique_hash = uuid.uuid4().hex
    handshake_payload = {
        "broker": "ICMarkets",
        "environment": "DEMO",
        "account_hash": unique_hash,
        "ea_identifier": "ALPED_BRIDGE_TEST",
        "ea_version": "3.0.0",
        "payload_version": "1.0.0",
        "schema_version": "1.0.0"
    }

    res = client.post("/api/v1/auth/handshake", json=handshake_payload, headers=AUTH_HEADERS)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "SUCCESS"
    source_id = data["source_id"]
    assert len(source_id) == 64

    # 2. Register Symbol
    symbol_payload = {
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
    }
    res = client.post("/api/v1/symbols", json=symbol_payload, headers=AUTH_HEADERS)
    assert res.status_code == 200

    # 3. Check Sync Status (Initially NO_DATA for new unique source)
    res = client.get(f"/api/v1/candles/sync-status?source_id={source_id}&symbol=EURUSD&timeframe=M5", headers=AUTH_HEADERS)
    assert res.status_code == 200
    assert res.json()["status"] == "NO_DATA"

    # 4. Ingest Candle Batch (Initial Sync)
    candle_batch = {
        "source_id": source_id,
        "symbol": "EURUSD",
        "timeframe": "M5",
        "sync_type": "INITIAL_SYNC",
        "candles": [
            {
                "candle_time_epoch": 1788917400,
                "candle_time_utc": "2026-09-09T01:30:00Z",
                "broker_time": "2026-09-09 04:30:00",
                "broker_gmt_offset": 10800,
                "open": "1.16800",
                "high": "1.16850",
                "low": "1.16780",
                "close": "1.16820",
                "tick_volume": 350,
                "spread": 1
            },
            {
                "candle_time_epoch": 1788917700,
                "candle_time_utc": "2026-09-09T01:35:00Z",
                "broker_time": "2026-09-09 04:35:00",
                "broker_gmt_offset": 10800,
                "open": "1.16820",
                "high": "1.16860",
                "low": "1.16810",
                "close": "1.16840",
                "tick_volume": 410,
                "spread": 1
            }
        ]
    }
    res = client.post("/api/v1/candles/batch", json=candle_batch, headers=AUTH_HEADERS)
    assert res.status_code == 200
    assert res.json()["processed_count"] == 2

    # Ingest same batch again (idempotent test)
    res = client.post("/api/v1/candles/batch", json=candle_batch, headers=AUTH_HEADERS)
    assert res.status_code == 200
    assert res.json()["processed_count"] == 2

    # 5. Live Candle Ingestion
    live_candle = {
        "source_id": source_id,
        "symbol": "EURUSD",
        "timeframe": "M5",
        "candle": {
            "candle_time_epoch": 1788918000,
            "candle_time_utc": "2026-09-09T01:40:00Z",
            "broker_time": "2026-09-09 04:40:00",
            "broker_gmt_offset": 10800,
            "open": "1.16840",
            "high": "1.16880",
            "low": "1.16830",
            "close": "1.16870",
            "tick_volume": 290,
            "spread": 1
        }
    }
    res = client.post("/api/v1/candles/live", json=live_candle, headers=AUTH_HEADERS)
    assert res.status_code == 200

    # 6. Verify Sync Status is now SYNCED
    res = client.get(f"/api/v1/candles/sync-status?source_id={source_id}&symbol=EURUSD&timeframe=M5", headers=AUTH_HEADERS)
    assert res.status_code == 200
    sync_data = res.json()
    assert sync_data["status"] == "SYNCED"
    assert sync_data["latest_candle_epoch"] == 1788918000

    # 7. Account Snapshot & Position Uniqueness
    snapshot_payload = {
        "source_id": source_id,
        "account": {
            "balance": "10000.00",
            "equity": "10050.00",
            "margin": "200.00",
            "free_margin": "9850.00",
            "margin_level": "5025.00",
            "open_positions_count": 1
        },
        "positions": [
            {
                "ticket": 999001,
                "symbol": "EURUSD",
                "type": "BUY",
                "lots": "0.50",
                "open_price": "1.16800",
                "open_time_utc": "2026-09-09T01:30:00Z",
                "sl": "1.16600",
                "tp": "1.17200",
                "current_price": "1.16840",
                "profit": "20.00",
                "magic_number": 0,
                "comment": "Test position"
            }
        ]
    }
    res = client.post("/api/v1/account/snapshot", json=snapshot_payload, headers=AUTH_HEADERS)
    assert res.status_code == 200

    # 8. Position Events from OnTradeTransaction()
    events_payload = {
        "source_id": source_id,
        "events": [
            {
                "ticket": 999001,
                "event_type": "OPEN",
                "symbol": "EURUSD",
                "lots": "0.50",
                "price": "1.16800",
                "sl": "1.16600",
                "tp": "1.17200",
                "profit": "0.00",
                "event_time_epoch": 1788917400,
                "event_time_utc": "2026-09-09T01:30:00Z"
            },
            {
                "ticket": 999001,
                "event_type": "MODIFY_SL",
                "symbol": "EURUSD",
                "lots": "0.50",
                "price": "1.16800",
                "sl": "1.16700",  # Trailed SL
                "tp": "1.17200",
                "profit": "10.00",
                "event_time_epoch": 1788917700,
                "event_time_utc": "2026-09-09T01:35:00Z"
            },
            {
                "ticket": 999001,
                "event_type": "CLOSE",
                "symbol": "EURUSD",
                "lots": "0.50",
                "price": "1.17200",
                "sl": "1.16700",
                "tp": "1.17200",
                "profit": "200.00",
                "event_time_epoch": 1788918000,
                "event_time_utc": "2026-09-09T01:40:00Z"
            }
        ]
    }
    res = client.post("/api/v1/positions/events", json=events_payload, headers=AUTH_HEADERS)
    assert res.status_code == 200
    assert "Logged 3 position events" in res.json()["message"]

    # 9. Health endpoint
    res = client.get("/api/v1/health")
    assert res.status_code == 200
    assert res.json()["status"] == "HEALTHY"
    assert res.json()["allow_execution"] is False

def test_position_isolation_different_sources():
    # Ticket 999001 in source A vs source B
    source_a = "a" * 64
    source_b = "b" * 64

    from backend.app.repositories.source_repo import source_repo
    source_repo.upsert_source({"source_id": source_a, "broker": "A", "environment": "DEMO", "account_hash": "a"*64, "ea_identifier": "A", "ea_version": "3.0.0", "payload_version": "1.0.0", "schema_version": "1.0.0"})
    source_repo.upsert_source({"source_id": source_b, "broker": "B", "environment": "DEMO", "account_hash": "b"*64, "ea_identifier": "B", "ea_version": "3.0.0", "payload_version": "1.0.0", "schema_version": "1.0.0"})

    payload_a = {
        "source_id": source_a,
        "account": {"balance": "1000", "equity": "1000", "margin": "0", "free_margin": "1000", "open_positions_count": 1},
        "positions": [{"ticket": 777, "symbol": "EURUSD", "type": "BUY", "lots": "1.0", "open_price": "1.1000", "open_time_utc": "2026-09-09T01:00:00Z", "current_price": "1.1010", "profit": "10.0"}]
    }
    payload_b = {
        "source_id": source_b,
        "account": {"balance": "2000", "equity": "2000", "margin": "0", "free_margin": "2000", "open_positions_count": 1},
        "positions": [{"ticket": 777, "symbol": "GBPUSD", "type": "SELL", "lots": "2.0", "open_price": "1.2500", "open_time_utc": "2026-09-09T01:00:00Z", "current_price": "1.2490", "profit": "20.0"}]
    }

    res_a = client.post("/api/v1/account/snapshot", json=payload_a, headers=AUTH_HEADERS)
    res_b = client.post("/api/v1/account/snapshot", json=payload_b, headers=AUTH_HEADERS)
    assert res_a.status_code == 200
    assert res_b.status_code == 200

    from backend.app.repositories.account_repo import _memory_positions
    # Both positions coexist because the key is (source_id, ticket)
    assert (source_a, 777) in _memory_positions
    assert (source_b, 777) in _memory_positions
    assert _memory_positions[(source_a, 777)]["symbol"] == "EURUSD"
    assert _memory_positions[(source_b, 777)]["symbol"] == "GBPUSD"
