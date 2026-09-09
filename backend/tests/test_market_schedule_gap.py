from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from backend.app.services.market_schedule_service import schedule_resolver
from backend.app.core.constants import GapStatus

def test_weekend_gap_classification():
    # Friday 17:05 NY time (21:05 UTC in EDT / 22:05 UTC in EST)
    ny_tz = ZoneInfo("America/New_York")
    # 2026-09-11 is a Friday
    friday_close_utc = datetime(2026, 9, 11, 21, 5, tzinfo=timezone.utc)
    # Sunday 16:55 NY time
    sunday_open_utc = datetime(2026, 9, 13, 20, 55, tzinfo=timezone.utc)

    status_tag, reason = schedule_resolver.classify_gap(
        gap_start_utc=friday_close_utc,
        gap_end_utc=sunday_open_utc,
        source_id="test_source",
        symbol="EURUSD"
    )

    assert status_tag == GapStatus.WEEKEND
    assert "Friday market close" in reason or "weekend" in reason.lower()

def test_maintenance_window_classification():
    # Tuesday 17:02 NY time (21:02 UTC EDT)
    tuesday_maint_start = datetime(2026, 9, 8, 21, 2, tzinfo=timezone.utc)
    tuesday_maint_end = datetime(2026, 9, 8, 21, 7, tzinfo=timezone.utc)

    status_tag, reason = schedule_resolver.classify_gap(
        gap_start_utc=tuesday_maint_start,
        gap_end_utc=tuesday_maint_end,
        source_id="test_source",
        symbol="EURUSD"
    )

    assert status_tag == GapStatus.BROKER_MAINTENANCE
    assert "maintenance" in reason.lower()

def test_holiday_gap_classification():
    # Christmas Day 2026-12-25
    xmas_start = datetime(2026, 12, 25, 12, 0, tzinfo=timezone.utc)
    xmas_end = datetime(2026, 12, 25, 14, 0, tzinfo=timezone.utc)

    status_tag, reason = schedule_resolver.classify_gap(
        gap_start_utc=xmas_start,
        gap_end_utc=xmas_end,
        source_id="test_source",
        symbol="EURUSD"
    )

    assert status_tag == GapStatus.HOLIDAY
    assert "holiday" in reason.lower()

def test_actual_missing_candle_classification():
    # Wednesday 10:00 NY time during peak London/NY overlap
    wed_active_start = datetime(2026, 9, 9, 14, 0, tzinfo=timezone.utc)
    wed_active_end = datetime(2026, 9, 9, 14, 30, tzinfo=timezone.utc)

    status_tag, reason = schedule_resolver.classify_gap(
        gap_start_utc=wed_active_start,
        gap_end_utc=wed_active_end,
        source_id="test_source",
        symbol="EURUSD"
    )

    assert status_tag == GapStatus.MISSING_DATA
    assert "Unscheduled" in reason
