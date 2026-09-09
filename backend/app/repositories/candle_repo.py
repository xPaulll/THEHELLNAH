import logging
from typing import Optional
from backend.app.services.supabase_service import get_supabase_client

logger = logging.getLogger(__name__)

_memory_candles: dict[tuple[str, str, str, str], dict] = {}
_memory_gaps: list[dict] = []

class CandleRepository:
    def upsert_candles(self, candles: list[dict]) -> int:
        if not candles:
            return 0

        # In-memory idempotent store
        for c in candles:
            key = (c["source_id"], c["symbol"], c["timeframe"], c["candle_time_utc"])
            _memory_candles[key] = c

        client = get_supabase_client()
        if client:
            try:
                res = client.table("market_candles").upsert(
                    candles,
                    on_conflict="source_id,symbol,timeframe,candle_time_utc"
                ).execute()
                if res.data:
                    return len(res.data)
            except Exception as e:
                logger.error(f"[CandleRepo] Supabase upsert error: {type(e).__name__}: {e}")

        return len(candles)

    def get_latest_candle(self, source_id: str, symbol: str, timeframe: str) -> Optional[dict]:
        client = get_supabase_client()
        if client:
            try:
                res = client.table("market_candles").select("*")\
                    .eq("source_id", source_id)\
                    .ilike("symbol", symbol)\
                    .eq("timeframe", timeframe)\
                    .order("candle_time_utc", desc=True)\
                    .limit(1)\
                    .execute()
                if res.data:
                    return res.data[0]
            except Exception:
                pass

        # Search in-memory
        matching = [
            c for (s_id, sym, tf, _), c in _memory_candles.items()
            if s_id == source_id and sym.upper() == symbol.upper() and tf.upper() == timeframe.upper()
        ]
        if not matching:
            return None
        return max(matching, key=lambda x: x["candle_time_epoch"])

    def record_gaps(self, gaps: list[dict]) -> int:
        if not gaps:
            return 0

        _memory_gaps.extend(gaps)

        client = get_supabase_client()
        if client:
            try:
                res = client.table("candle_gaps").insert(gaps).execute()
                if res.data:
                    return len(res.data)
            except Exception as e:
                logger.error(f"[CandleRepo] Supabase record_gaps error: {e}")

        return len(gaps)

candle_repo = CandleRepository()
