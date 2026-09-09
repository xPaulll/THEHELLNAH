from decimal import Decimal
from typing import Optional
from datetime import datetime, timezone
from fastapi import HTTPException, status
from backend.app.models.market_data import CandleItem
from backend.app.core.constants import TIMEFRAME_SECONDS, GapStatus
from backend.app.services.market_schedule_service import schedule_resolver
from backend.app.services.timezone_service import generate_canonical_utc

def validate_candle_sanity(candle: CandleItem, expected_digits: Optional[int] = None) -> None:
    """
    Validates mathematical and structural sanity of an incoming candle.
    Raises HTTPException 422 if candle is corrupt.
    """
    o, h, l, c = candle.open, candle.high, candle.low, candle.close

    if h < l:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Price sanity failure: High ({h}) cannot be less than Low ({l})"
        )

    if not (l <= o <= h):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Price sanity failure: Open ({o}) is outside [Low={l}, High={h}]"
        )

    if not (l <= c <= h):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Price sanity failure: Close ({c}) is outside [Low={l}, High={h}]"
        )

    if h <= Decimal("0") or l <= Decimal("0"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Price sanity failure: Prices must be strictly positive (High={h}, Low={l})"
        )

    if candle.tick_volume < 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Volume sanity failure: tick_volume ({candle.tick_volume}) cannot be negative"
        )

    if candle.spread < 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Spread sanity failure: spread ({candle.spread}) cannot be negative"
        )

    # Validate precision if expected_digits provided
    if expected_digits is not None:
        for price_val, name in [(o, "Open"), (h, "High"), (l, "Low"), (c, "Close")]:
            # Convert to normalized string to check decimal places
            d_tuple = price_val.as_tuple()
            num_decimals = -d_tuple.exponent if d_tuple.exponent < 0 else 0
            if num_decimals > expected_digits:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Precision sanity failure: {name} ({price_val}) has {num_decimals} decimals, expected at most {expected_digits}"
                )

def detect_timeline_gaps(
    candles: list[CandleItem],
    timeframe: str,
    source_id: str,
    symbol: str
) -> list[dict]:
    """
    Scans a sorted list of candles for timeline intervals greater than expected.
    Returns list of classified gap records.
    """
    if len(candles) < 2:
        return []

    expected_interval = TIMEFRAME_SECONDS.get(timeframe, 60)
    sorted_candles = sorted(candles, key=lambda x: x.candle_time_epoch)

    detected_gaps: list[dict] = []

    for i in range(len(sorted_candles) - 1):
        c1 = sorted_candles[i]
        c2 = sorted_candles[i + 1]

        delta = c2.candle_time_epoch - c1.candle_time_epoch
        if delta > expected_interval:
            start_utc = generate_canonical_utc(c1.candle_time_epoch)
            end_utc = generate_canonical_utc(c2.candle_time_epoch)
            missing_count = (delta // expected_interval) - 1

            status_tag, reason = schedule_resolver.classify_gap(
                gap_start_utc=start_utc,
                gap_end_utc=end_utc,
                source_id=source_id,
                symbol=symbol
            )

            detected_gaps.append({
                "source_id": source_id,
                "symbol": symbol,
                "timeframe": timeframe,
                "gap_start_utc": start_utc.isoformat(),
                "gap_end_utc": end_utc.isoformat(),
                "expected_interval_seconds": expected_interval,
                "missing_bars_count": missing_count,
                "status": status_tag.value,
                "classification_reason": reason,
                "detected_at": datetime.now(timezone.utc).isoformat()
            })

    return detected_gaps
