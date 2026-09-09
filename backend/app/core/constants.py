from enum import Enum

class Timeframe(str, Enum):
    M1 = "M1"
    M5 = "M5"
    M15 = "M15"
    M30 = "M30"
    H1 = "H1"
    H4 = "H4"
    D1 = "D1"

TIMEFRAME_SECONDS: dict[str, int] = {
    "M1": 60,
    "M5": 300,
    "M15": 900,
    "M30": 1800,
    "H1": 3600,
    "H4": 14400,
    "D1": 86400,
}

class SessionTag(str, Enum):
    ASIA = "ASIA"
    LONDON = "LONDON"
    NEW_YORK = "NEW_YORK"
    LONDON_NY_OVERLAP = "LONDON_NY_OVERLAP"
    LONDON_SILVER_BULLET = "LONDON_SILVER_BULLET"
    NY_AM_SILVER_BULLET = "NY_AM_SILVER_BULLET"
    NY_PM_SILVER_BULLET = "NY_PM_SILVER_BULLET"
    OFF_SESSION = "OFF_SESSION"

class GapStatus(str, Enum):
    DETECTED = "DETECTED"
    RECOVERED = "RECOVERED"
    WEEKEND = "WEEKEND"
    MARKET_CLOSED = "MARKET_CLOSED"
    HOLIDAY = "HOLIDAY"
    BROKER_MAINTENANCE = "BROKER_MAINTENANCE"
    PERMANENT_UNAVAILABLE = "PERMANENT_UNAVAILABLE"
    MISSING_DATA = "MISSING_DATA"

class PositionEventType(str, Enum):
    OPEN = "OPEN"
    MODIFY_SL = "MODIFY_SL"
    MODIFY_TP = "MODIFY_TP"
    PARTIAL_CLOSE = "PARTIAL_CLOSE"
    CLOSE = "CLOSE"

class StrategyMode(str, Enum):
    INTRADAY = "INTRADAY"
    SILVER_BULLET = "SILVER_BULLET"
    BOTH = "BOTH"

class SyncType(str, Enum):
    INITIAL_SYNC = "INITIAL_SYNC"
    RECOVERY_SYNC = "RECOVERY_SYNC"
    GAP_BACKFILL = "GAP_BACKFILL"
    LIVE_SYNC = "LIVE_SYNC"

class TickMode(str, Enum):
    TICK_OFF = "TICK_OFF"
    TICK_SAMPLE = "TICK_SAMPLE"
    TICK_FULL = "TICK_FULL"
