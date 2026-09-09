from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from fastapi import HTTPException, status

TZ_UTC = ZoneInfo("UTC")
TZ_JAKARTA = ZoneInfo("Asia/Jakarta")
TZ_LONDON = ZoneInfo("Europe/London")
TZ_NEW_YORK = ZoneInfo("America/New_York")
TZ_TOKYO = ZoneInfo("Asia/Tokyo")

def generate_canonical_utc(candle_time_epoch: int) -> datetime:
    """
    Generates canonical timezone-aware UTC datetime from authoritative MT5 epoch.
    """
    return datetime.fromtimestamp(candle_time_epoch, tz=TZ_UTC)

def parse_iso_or_sql_timestamp(ts_str: str) -> datetime:
    """
    Parses an ISO-8601 or standard SQL timestamp string into a timezone-aware UTC datetime.
    """
    cleaned = ts_str.strip().replace("Z", "+00:00")
    if " " in cleaned and "T" not in cleaned:
        cleaned = cleaned.replace(" ", "T")
    dt = datetime.fromisoformat(cleaned)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ_UTC)
    else:
        dt = dt.astimezone(TZ_UTC)
    return dt

def verify_timestamp_consistency(
    candle_time_epoch: int,
    client_utc_str: str,
    broker_time_str: str,
    broker_gmt_offset: int
) -> datetime:
    """
    3-way validation for timestamp integrity:
    1. epoch must match generated canonical UTC.
    2. client_utc_str parsed must match epoch.
    3. broker_time - broker_gmt_offset must equal epoch.

    Returns the authoritative canonical UTC datetime.
    Raises HTTPException 422 if inconsistent.
    """
    canonical_utc = generate_canonical_utc(candle_time_epoch)

    # 1. Validate client UTC string
    try:
        parsed_client_utc = parse_iso_or_sql_timestamp(client_utc_str)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid candle_time_utc format '{client_utc_str}': {str(e)}"
        )

    delta_client = abs(int(parsed_client_utc.timestamp()) - candle_time_epoch)
    if delta_client > 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Timestamp inconsistency: candle_time_epoch ({candle_time_epoch}) does not match "
                f"candle_time_utc parsed timestamp ({int(parsed_client_utc.timestamp())}). Delta: {delta_client}s"
            )
        )

    # 2. Validate broker_time - broker_gmt_offset
    try:
        # broker_time is sent as local time e.g. "2026-09-09 04:35:00" or ISO format
        cleaned_broker = broker_time_str.strip().replace("T", " ")
        parsed_broker = datetime.fromisoformat(cleaned_broker)
        # Calculate epoch of broker_time assuming it is at broker local wall clock
        # Wall clock seconds - offset in seconds = UTC seconds
        broker_local_epoch = int(parsed_broker.replace(tzinfo=timezone.utc).timestamp())
        computed_utc_epoch = broker_local_epoch - broker_gmt_offset
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid broker_time format '{broker_time_str}': {str(e)}"
        )

    delta_broker = abs(computed_utc_epoch - candle_time_epoch)
    if delta_broker > 1:  # Allow 1 second tolerance for potential rounding/sub-second leap
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Timestamp inconsistency: broker_time ({broker_time_str}) minus offset ({broker_gmt_offset}s) "
                f"results in epoch {computed_utc_epoch}, but authoritative candle_time_epoch is {candle_time_epoch}. Delta: {delta_broker}s"
            )
        )

    return canonical_utc
