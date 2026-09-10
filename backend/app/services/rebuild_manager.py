import threading
import logging
from backend.app.core.constants import canonicalize_symbol

logger = logging.getLogger(__name__)

class RebuildManager:
    """
    Manages per-key locks and synchronization status for feature engine rebuilds.
    Guarantees that for any (source_id, symbol, timeframe), only one rebuild
    is executing at any given time, preventing race conditions, duplicate processing,
    and database write collisions.
    """
    def __init__(self):
        self._locks: dict[tuple[str, str, str], threading.Lock] = {}
        self._active_rebuilds: set[tuple[str, str, str]] = set()
        self._global_lock = threading.Lock()

    def _get_key(self, source_id: str, symbol: str, timeframe: str) -> tuple[str, str, str]:
        return (source_id, canonicalize_symbol(symbol), timeframe.upper())

    def get_lock(self, source_id: str, symbol: str, timeframe: str) -> threading.Lock:
        key = self._get_key(source_id, symbol, timeframe)
        with self._global_lock:
            if key not in self._locks:
                self._locks[key] = threading.Lock()
            return self._locks[key]

    def is_rebuilding(self, source_id: str, symbol: str, timeframe: str) -> bool:
        key = self._get_key(source_id, symbol, timeframe)
        with self._global_lock:
            return key in self._active_rebuilds

    def mark_started(self, source_id: str, symbol: str, timeframe: str) -> None:
        key = self._get_key(source_id, symbol, timeframe)
        with self._global_lock:
            self._active_rebuilds.add(key)
        logger.info(f"[RebuildManager] Started feature rebuild lock for {key}")

    def mark_completed(self, source_id: str, symbol: str, timeframe: str) -> None:
        key = self._get_key(source_id, symbol, timeframe)
        with self._global_lock:
            self._active_rebuilds.discard(key)
        logger.info(f"[RebuildManager] Released feature rebuild lock for {key}")

rebuild_manager = RebuildManager()
