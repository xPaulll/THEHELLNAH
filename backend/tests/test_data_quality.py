import pytest
from decimal import Decimal
from fastapi import HTTPException
from backend.app.models.market_data import CandleItem
from backend.app.services.data_quality_service import validate_candle_sanity

def test_valid_candle():
    candle = CandleItem(
        candle_time_epoch=1788917400,
        candle_time_utc="2026-09-09T01:30:00Z",
        broker_time="2026-09-09 04:30:00",
        broker_gmt_offset=10800,
        open=Decimal("1.16800"),
        high=Decimal("1.16850"),
        low=Decimal("1.16780"),
        close=Decimal("1.16820"),
        tick_volume=250,
        spread=1
    )
    # Should not raise
    validate_candle_sanity(candle, expected_digits=5)

def test_high_less_than_low_rejected():
    candle = CandleItem(
        candle_time_epoch=1788917400,
        candle_time_utc="2026-09-09T01:30:00Z",
        broker_time="2026-09-09 04:30:00",
        broker_gmt_offset=10800,
        open=Decimal("1.16800"),
        high=Decimal("1.16700"),  # High < Low!
        low=Decimal("1.16780"),
        close=Decimal("1.16750"),
        tick_volume=250,
        spread=1
    )
    with pytest.raises(HTTPException) as exc_info:
        validate_candle_sanity(candle)
    assert exc_info.value.status_code == 422
    assert "High" in exc_info.value.detail and "Low" in exc_info.value.detail

def test_open_outside_high_low_rejected():
    candle = CandleItem(
        candle_time_epoch=1788917400,
        candle_time_utc="2026-09-09T01:30:00Z",
        broker_time="2026-09-09 04:30:00",
        broker_gmt_offset=10800,
        open=Decimal("1.16900"),  # Open > High!
        high=Decimal("1.16850"),
        low=Decimal("1.16780"),
        close=Decimal("1.16820"),
        tick_volume=250,
        spread=1
    )
    with pytest.raises(HTTPException) as exc_info:
        validate_candle_sanity(candle)
    assert exc_info.value.status_code == 422
    assert "Open" in exc_info.value.detail

def test_negative_spread_rejected():
    candle = CandleItem(
        candle_time_epoch=1788917400,
        candle_time_utc="2026-09-09T01:30:00Z",
        broker_time="2026-09-09 04:30:00",
        broker_gmt_offset=10800,
        open=Decimal("1.16800"),
        high=Decimal("1.16850"),
        low=Decimal("1.16780"),
        close=Decimal("1.16820"),
        tick_volume=250,
        spread=-5  # Negative spread!
    )
    with pytest.raises(HTTPException) as exc_info:
        validate_candle_sanity(candle)
    assert exc_info.value.status_code == 422
    assert "spread" in exc_info.value.detail
