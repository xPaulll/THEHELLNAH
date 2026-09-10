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

class SwingType(str, Enum):
    HIGH = "HIGH"
    LOW = "LOW"

class SwingClassification(str, Enum):
    HH = "HH"
    HL = "HL"
    LH = "LH"
    LL = "LL"
    EQH = "EQH"
    EQL = "EQL"

DEFAULT_EQUAL_TOLERANCE_POINTS: dict[str, float] = {
    "XAUUSD": 10.0,  # 10 points (e.g. 10 * 0.01 = $0.10)
    "EURUSD": 30.0,  # 30 points (e.g. 30 * 0.00001 = 0.00030 = 3 pips)
    "GBPUSD": 30.0,  # 30 points (e.g. 30 * 0.00001 = 0.00030 = 3 pips)
    "DEFAULT": 10.0
}

class StructureBias(str, Enum):
    NEUTRAL = "NEUTRAL"
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"

class StructureTransitionState(str, Enum):
    NORMAL = "NORMAL"
    CHOCH_BEARISH_PENDING = "CHOCH_BEARISH_PENDING"
    CHOCH_BULLISH_PENDING = "CHOCH_BULLISH_PENDING"

class StructureEventType(str, Enum):
    BOS_BULLISH = "BOS_BULLISH"
    BOS_BEARISH = "BOS_BEARISH"
    CHOCH_BULLISH = "CHOCH_BULLISH"
    CHOCH_BEARISH = "CHOCH_BEARISH"
    MSS_BULLISH = "MSS_BULLISH"
    MSS_BEARISH = "MSS_BEARISH"
    DOUBLE_BREAK = "DOUBLE_BREAK"

STRUCTURE_BREAK_TOLERANCE_POINTS: dict[str, float] = {
    "XAUUSD": 0.0,
    "EURUSD": 0.0,
    "GBPUSD": 0.0,
    "DEFAULT": 0.0
}

def canonicalize_symbol(symbol: str) -> str:
    """Canonical uppercase format for instruments (e.g. XAUUSD.vx -> XAUUSD.VX)"""
    return symbol.strip().upper()

