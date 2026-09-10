import logging
from typing import Optional
from backend.app.services.supabase_service import get_supabase_client

logger = logging.getLogger(__name__)

# In-memory store for unit test isolation: (source_id, symbol_upper, timeframe_upper, epoch, swing_type) -> swing_dict
_memory_swings: dict[tuple[str, str, str, int, str], dict] = {}

class SwingRepository:
    def upsert_swings(self, swings: list[dict]) -> int:
        if not swings:
            return 0

        # In-memory idempotent store
        for s in swings:
            key = (
                s["source_id"],
                s["symbol"].upper(),
                s["timeframe"].upper(),
                s["swing_candle_time_epoch"],
                s["swing_type"]
            )
            _memory_swings[key] = s

        client = get_supabase_client()
        if client:
            try:
                res = client.table("market_swings").upsert(
                    swings,
                    on_conflict="source_id,symbol,timeframe,swing_candle_time_epoch,swing_type"
                ).execute()
                if res.data:
                    return len(res.data)
            except Exception as e:
                logger.error(f"[SwingRepo] Supabase upsert error: {type(e).__name__}: {e}")

        return len(swings)

    def get_swings(
        self,
        source_id: str,
        symbol: str,
        timeframe: str,
        limit: int = 100
    ) -> list[dict]:
        """
        Returns swings ordered by swing_candle_time_epoch DESC.
        Case-insensitive symbol matching.
        """
        client = get_supabase_client()
        if client:
            try:
                res = client.table("market_swings").select("*")\
                    .eq("source_id", source_id)\
                    .ilike("symbol", symbol)\
                    .eq("timeframe", timeframe)\
                    .order("swing_candle_time_epoch", desc=True)\
                    .limit(limit)\
                    .execute()
                if res.data:
                    return res.data
            except Exception as e:
                logger.error(f"[SwingRepo] Supabase get_swings error: {e}")

        # In-memory fallback
        matching = [
            s for (s_id, sym_u, tf_u, _, _), s in _memory_swings.items()
            if s_id == source_id and sym_u == symbol.upper() and tf_u == timeframe.upper()
        ]
        matching.sort(key=lambda x: x["swing_candle_time_epoch"], reverse=True)
        return matching[:limit]

    def get_latest_swing(
        self,
        source_id: str,
        symbol: str,
        timeframe: str,
        swing_type: str,
        before_epoch: Optional[int] = None
    ) -> Optional[dict]:
        """
        Retrieves the single most recent confirmed swing of a given type (HIGH or LOW),
        optionally strictly before a specified candle epoch.
        Used by incremental live processing to classify new swings without full re-scan.
        """
        client = get_supabase_client()
        if client:
            try:
                query = client.table("market_swings").select("*")\
                    .eq("source_id", source_id)\
                    .ilike("symbol", symbol)\
                    .eq("timeframe", timeframe)\
                    .eq("swing_type", swing_type)

                if before_epoch is not None:
                    query = query.lt("swing_candle_time_epoch", before_epoch)

                res = query.order("swing_candle_time_epoch", desc=True).limit(1).execute()
                if res.data and len(res.data) > 0:
                    return res.data[0]
            except Exception as e:
                logger.error(f"[SwingRepo] Supabase get_latest_swing error: {e}")

        # In-memory fallback
        matching = [
            s for (s_id, sym_u, tf_u, _, s_type), s in _memory_swings.items()
            if s_id == source_id and sym_u == symbol.upper() and tf_u == timeframe.upper() and s_type == swing_type
        ]
        if before_epoch is not None:
            matching = [s for s in matching if s["swing_candle_time_epoch"] < before_epoch]

        if not matching:
            return None
        return max(matching, key=lambda x: x["swing_candle_time_epoch"])

    def get_confirmed_swings_ascending(
        self,
        source_id: str,
        symbol: str,
        timeframe: str,
        limit: int = 10000
    ) -> list[dict]:
        """
        Retrieves confirmed swings ordered deterministically ascending:
        1. confirmed_at ASC
        2. swing_candle_time ASC
        3. swing_type ASC
        4. id ASC
        Used for Full Scan Step 2 rebuilding.
        """
        client = get_supabase_client()
        if client:
            try:
                res = client.table("market_swings").select("*")\
                    .eq("source_id", source_id)\
                    .ilike("symbol", symbol)\
                    .eq("timeframe", timeframe)\
                    .order("confirmed_at_candle_time_epoch", desc=False)\
                    .order("swing_candle_time_epoch", desc=False)\
                    .order("swing_type", desc=False)\
                    .limit(limit)\
                    .execute()
                if res.data:
                    return res.data
            except Exception as e:
                logger.error(f"[SwingRepo] Supabase get_confirmed_swings_ascending error: {e}")

        # In-memory fallback
        matching = [
            s for (s_id, sym_u, tf_u, _, _), s in _memory_swings.items()
            if s_id == source_id and sym_u == symbol.upper() and tf_u == timeframe.upper()
        ]
        matching.sort(key=lambda x: (
            int(x.get("confirmed_at_candle_time_epoch", 0)),
            int(x.get("swing_candle_time_epoch", 0)),
            str(x.get("swing_type", "")),
            int(x.get("id", 0))
        ))
        return matching[:limit]

    def get_newly_confirmed_swings(
        self,
        source_id: str,
        symbol: str,
        timeframe: str,
        after_confirmation_time: int,
        up_to_candle_time: int
    ) -> list[dict]:
        """
        Retrieves newly confirmed swings for incremental processing:
        confirmed_at > after_confirmation_time AND confirmed_at <= up_to_candle_time.
        Ordered deterministically.
        """
        client = get_supabase_client()
        if client:
            try:
                res = client.table("market_swings").select("*")\
                    .eq("source_id", source_id)\
                    .ilike("symbol", symbol)\
                    .eq("timeframe", timeframe)\
                    .gt("confirmed_at_candle_time_epoch", after_confirmation_time)\
                    .lte("confirmed_at_candle_time_epoch", up_to_candle_time)\
                    .order("confirmed_at_candle_time_epoch", desc=False)\
                    .order("swing_candle_time_epoch", desc=False)\
                    .order("swing_type", desc=False)\
                    .execute()
                if res.data:
                    return res.data
            except Exception as e:
                logger.error(f"[SwingRepo] Supabase get_newly_confirmed_swings error: {e}")

        # In-memory fallback
        matching = [
            s for (s_id, sym_u, tf_u, _, _), s in _memory_swings.items()
            if s_id == source_id and sym_u == symbol.upper() and tf_u == timeframe.upper()
            and int(s.get("confirmed_at_candle_time_epoch", 0)) > after_confirmation_time
            and int(s.get("confirmed_at_candle_time_epoch", 0)) <= up_to_candle_time
        ]
        matching.sort(key=lambda x: (
            int(x.get("confirmed_at_candle_time_epoch", 0)),
            int(x.get("swing_candle_time_epoch", 0)),
            str(x.get("swing_type", "")),
            int(x.get("id", 0))
        ))
        return matching

swing_repo = SwingRepository()
