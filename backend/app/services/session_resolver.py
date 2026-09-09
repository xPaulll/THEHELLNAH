from datetime import datetime, time
from zoneinfo import ZoneInfo
from backend.app.core.constants import SessionTag
from backend.app.services.timezone_service import TZ_LONDON, TZ_NEW_YORK, TZ_TOKYO

def resolve_session_tags(utc_dt: datetime) -> list[str]:
    """
    Evaluates active trading sessions and Silver Bullet windows from canonical UTC datetime
    using timezone-aware zoneinfo conversions (fully DST-aware).
    """
    tags: list[str] = []

    # 1. Tokyo / Asia Session
    tokyo_dt = utc_dt.astimezone(TZ_TOKYO)
    if time(9, 0) <= tokyo_dt.time() < time(15, 0):
        tags.append(SessionTag.ASIA.value)

    # 2. London Session
    london_dt = utc_dt.astimezone(TZ_LONDON)
    if time(8, 0) <= london_dt.time() < time(16, 30):
        tags.append(SessionTag.LONDON.value)

    # London Silver Bullet Window (08:00 - 09:00 London time)
    if time(8, 0) <= london_dt.time() < time(9, 0):
        tags.append(SessionTag.LONDON_SILVER_BULLET.value)

    # 3. New York Session
    ny_dt = utc_dt.astimezone(TZ_NEW_YORK)
    if time(8, 0) <= ny_dt.time() < time(17, 0):
        tags.append(SessionTag.NEW_YORK.value)

    # London / New York Overlap (08:00 - 12:00 NY time)
    if time(8, 0) <= ny_dt.time() < time(12, 0):
        tags.append(SessionTag.LONDON_NY_OVERLAP.value)

    # NY AM Silver Bullet Window (10:00 - 11:00 NY time)
    if time(10, 0) <= ny_dt.time() < time(11, 0):
        tags.append(SessionTag.NY_AM_SILVER_BULLET.value)

    # NY PM Silver Bullet Window (14:00 - 15:00 NY time)
    if time(14, 0) <= ny_dt.time() < time(15, 0):
        tags.append(SessionTag.NY_PM_SILVER_BULLET.value)

    if not tags:
        tags.append(SessionTag.OFF_SESSION.value)

    return tags

def get_primary_session(utc_dt: datetime) -> str:
    """
    Returns the primary session string for database storage.
    Prioritizes Overlap and Silver Bullet windows if active.
    """
    tags = resolve_session_tags(utc_dt)
    # If Silver Bullet window is active, prioritize it or join
    return ",".join(tags)
