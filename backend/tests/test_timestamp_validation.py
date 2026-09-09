import pytest
from datetime import datetime, timezone
from fastapi import HTTPException
from backend.app.services.timezone_service import verify_timestamp_consistency, generate_canonical_utc

def test_valid_timestamp_consistency():
    # Epoch for 2026-09-09 01:30:00 UTC
    epoch = 1788917400
    client_utc_str = "2026-09-09T01:30:00Z"
    # Broker GMT+3 -> 04:30:00 local time, offset = 10800s
    broker_time_str = "2026-09-09 04:30:00"
    broker_gmt_offset = 10800

    canonical_utc = verify_timestamp_consistency(
        candle_time_epoch=epoch,
        client_utc_str=client_utc_str,
        broker_time_str=broker_time_str,
        broker_gmt_offset=broker_gmt_offset
    )

    assert canonical_utc == datetime.fromtimestamp(epoch, tz=timezone.utc)
    assert canonical_utc.year == 2026

def test_epoch_vs_utc_mismatch():
    epoch = 1788917400  # 01:30:00 UTC
    mismatched_utc_str = "2026-09-09T02:30:00Z"  # 1 hour off
    broker_time_str = "2026-09-09 04:30:00"
    broker_gmt_offset = 10800

    with pytest.raises(HTTPException) as exc_info:
        verify_timestamp_consistency(
            candle_time_epoch=epoch,
            client_utc_str=mismatched_utc_str,
            broker_time_str=broker_time_str,
            broker_gmt_offset=broker_gmt_offset
        )
    assert exc_info.value.status_code == 422
    assert "Timestamp inconsistency" in exc_info.value.detail

def test_broker_time_vs_offset_mismatch():
    epoch = 1788917400  # 01:30:00 UTC
    client_utc_str = "2026-09-09T01:30:00Z"
    # Wrong broker time: 05:30:00 with offset 10800 -> results in 02:30:00 UTC
    wrong_broker_time_str = "2026-09-09 05:30:00"
    broker_gmt_offset = 10800

    with pytest.raises(HTTPException) as exc_info:
        verify_timestamp_consistency(
            candle_time_epoch=epoch,
            client_utc_str=client_utc_str,
            broker_time_str=wrong_broker_time_str,
            broker_gmt_offset=broker_gmt_offset
        )
    assert exc_info.value.status_code == 422
    assert "broker_time" in exc_info.value.detail
