import time
import threading
from datetime import datetime, timezone
from typing import Optional, Tuple
from backend.app.models.live_market import LiveMarketPayload, Bar0Data, TickData

class LiveMarketManager:
    def __init__(self):
        self._lock = threading.RLock()
        self._source_id: Optional[str] = None
        self._symbol: str = "XAUUSD.vx"
        self._latest_tick: Optional[dict] = None
        self._forming_candles: dict[str, dict] = {}
        self._last_update_mono: float = 0.0
        self._last_update_utc: Optional[datetime] = None

        # Stale detection thresholds (in seconds)
        self.LIVE_THRESHOLD = 3.0
        self.STALE_THRESHOLD = 10.0

        # Previous state cache for deduplication
        self._last_state_hash_tuple: Optional[tuple] = None

    def update_live_market(self, payload: LiveMarketPayload) -> bool:
        """
        Updates in-memory live market state and returns True if state meaningfully changed,
        or False if duplicate / unchanged (deduplication).
        """
        now_mono = time.monotonic()
        now_utc = datetime.now(timezone.utc)

        # Build comparison tuple for fast deduplication without deepcopy
        # Includes tick metrics and all timeframe bar OHLCV
        tf_tuples = []
        for tf in sorted(payload.timeframes.keys()):
            bar = payload.timeframes[tf]
            tf_tuples.append((
                tf,
                bar.time_epoch,
                round(bar.open, 4),
                round(bar.high, 4),
                round(bar.low, 4),
                round(bar.close, 4),
                bar.volume
            ))

        state_tuple = (
            payload.symbol,
            round(payload.tick.bid, 4),
            round(payload.tick.ask, 4),
            payload.tick.spread,
            tuple(tf_tuples)
        )

        with self._lock:
            # Always update timestamps and IDs
            self._source_id = payload.source_id
            self._symbol = payload.symbol
            self._last_update_mono = now_mono
            self._last_update_utc = now_utc

            is_changed = (self._last_state_hash_tuple != state_tuple)
            self._last_state_hash_tuple = state_tuple

            # Update in-memory structures
            self._latest_tick = {
                "bid": payload.tick.bid,
                "ask": payload.tick.ask,
                "spread": payload.tick.spread,
                "tick_time_utc": payload.tick.tick_time_utc,
                "tick_time_epoch": payload.tick.tick_time_epoch or int(now_utc.timestamp()),
                "received_at": now_utc.isoformat()
            }

            for tf, b in payload.timeframes.items():
                self._forming_candles[tf] = {
                    "timeframe": tf,
                    "time_epoch": b.time_epoch,
                    "time_utc": b.time_utc,
                    "open": b.open,
                    "high": b.high,
                    "low": b.low,
                    "close": b.close,
                    "volume": b.volume,
                    "updated_at": now_utc.isoformat()
                }

            return is_changed

    def get_market_liveness(self) -> Tuple[str, float]:
        """
        Evaluates data freshness based on last_update_mono.
        Returns (status, age_seconds) where status in ('LIVE', 'STALE', 'OFFLINE').
        """
        with self._lock:
            if self._last_update_mono == 0.0:
                return "OFFLINE", 0.0
            
            age = time.monotonic() - self._last_update_mono
            if age < self.LIVE_THRESHOLD:
                return "LIVE", round(age, 2)
            elif age <= self.STALE_THRESHOLD:
                return "STALE", round(age, 2)
            else:
                return "OFFLINE", round(age, 2)

    def get_snapshot(self) -> dict:
        """
        Returns full live market snapshot for REST response or initial WebSocket payload.
        """
        with self._lock:
            liveness_status, age_sec = self.get_market_liveness()
            return {
                "symbol": self._symbol,
                "source_id": self._source_id,
                "liveness": {
                    "status": liveness_status,
                    "age_seconds": age_sec,
                    "is_live": (liveness_status == "LIVE"),
                    "last_update_utc": self._last_update_utc.isoformat() if self._last_update_utc else None
                },
                "tick": dict(self._latest_tick) if self._latest_tick else None,
                "timeframes": {tf: dict(val) for tf, val in self._forming_candles.items()}
            }

live_market_manager = LiveMarketManager()
