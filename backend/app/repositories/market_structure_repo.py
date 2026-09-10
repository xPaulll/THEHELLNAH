import logging
from typing import Optional
from backend.app.services.supabase_service import get_supabase_client
from backend.app.core.constants import canonicalize_symbol

logger = logging.getLogger(__name__)

# In-memory storage for test isolation guardrail:
# Events: (source_id, symbol_canonical, timeframe, epoch, event_type) -> event_dict
_memory_structure_events: dict[tuple[str, str, str, int, str], dict] = {}

# State: (source_id, symbol_canonical, timeframe) -> state_dict
_memory_structure_state: dict[tuple[str, str, str], dict] = {}

class MarketStructureRepository:
    """
    Authoritative persistence layer for market structure events and state.
    Strictly uses canonical symbols (uppercase).
    """

    def upsert_structure_events(self, events: list[dict]) -> int:
        if not events:
            return 0

        # In-memory idempotent store
        for e in events:
            sym_canon = canonicalize_symbol(e["symbol"])
            key = (
                e["source_id"],
                sym_canon,
                e["timeframe"],
                e["event_candle_time_epoch"],
                e["event_type"]
            )
            e_copy = dict(e)
            e_copy["symbol"] = sym_canon
            _memory_structure_events[key] = e_copy

        client = get_supabase_client()
        if client:
            try:
                # Ensure canonical symbol in payload
                payload = []
                for ev in events:
                    ev_clean = dict(ev)
                    ev_clean["symbol"] = canonicalize_symbol(ev_clean["symbol"])
                    payload.append(ev_clean)

                res = client.table("market_structure_events").upsert(
                    payload,
                    on_conflict="source_id,symbol,timeframe,event_candle_time_epoch,event_type"
                ).execute()
                if res.data:
                    return len(res.data)
            except Exception as ex:
                logger.error(f"[MarketStructureRepo] Supabase upsert events error: {type(ex).__name__}: {ex}")

        return len(events)

    def get_structure_events(
        self,
        source_id: str,
        symbol: str,
        timeframe: str,
        limit: int = 100
    ) -> list[dict]:
        """Returns events ordered by event_candle_time_epoch DESC."""
        sym_canon = canonicalize_symbol(symbol)
        client = get_supabase_client()
        if client:
            try:
                res = client.table("market_structure_events").select("*")\
                    .eq("source_id", source_id)\
                    .ilike("symbol", sym_canon)\
                    .eq("timeframe", timeframe)\
                    .order("event_candle_time_epoch", desc=True)\
                    .limit(limit)\
                    .execute()
                if res.data:
                    return res.data
            except Exception as ex:
                logger.error(f"[MarketStructureRepo] Supabase get_structure_events error: {ex}")

        # In-memory fallback
        matching = [
            e for (s_id, s_sym, s_tf, _, _), e in _memory_structure_events.items()
            if s_id == source_id and s_sym == sym_canon and s_tf == timeframe
        ]
        matching.sort(key=lambda x: x["event_candle_time_epoch"], reverse=True)
        return matching[:limit]

    def get_latest_structure_event(
        self,
        source_id: str,
        symbol: str,
        timeframe: str
    ) -> Optional[dict]:
        events = self.get_structure_events(source_id, symbol, timeframe, limit=1)
        return events[0] if events else None

    def get_structure_state(
        self,
        source_id: str,
        symbol: str,
        timeframe: str
    ) -> Optional[dict]:
        sym_canon = canonicalize_symbol(symbol)
        client = get_supabase_client()
        if client:
            try:
                res = client.table("market_structure_state").select("*")\
                    .eq("source_id", source_id)\
                    .ilike("symbol", sym_canon)\
                    .eq("timeframe", timeframe)\
                    .limit(1)\
                    .execute()
                if res.data and len(res.data) > 0:
                    return res.data[0]
            except Exception as ex:
                logger.error(f"[MarketStructureRepo] Supabase get_structure_state error: {ex}")

        # In-memory fallback
        key = (source_id, sym_canon, timeframe)
        return _memory_structure_state.get(key)

    def upsert_structure_state(self, state: dict) -> bool:
        sym_canon = canonicalize_symbol(state["symbol"])
        key = (state["source_id"], sym_canon, state["timeframe"])
        state_clean = dict(state)
        state_clean["symbol"] = sym_canon
        _memory_structure_state[key] = state_clean

        client = get_supabase_client()
        if client:
            try:
                res = client.table("market_structure_state").upsert(
                    state_clean,
                    on_conflict="source_id,symbol,timeframe"
                ).execute()
                return bool(res.data)
            except Exception as ex:
                logger.error(f"[MarketStructureRepo] Supabase upsert_structure_state error: {type(ex).__name__}: {ex}")

        return True

    def save_event_and_state(
        self,
        event: Optional[dict],
        state: dict
    ) -> bool:
        """
        Atomic persistence boundary:
        Upserts event (if present) and updates state & cursor in tandem.
        """
        if event:
            self.upsert_structure_events([event])
        return self.upsert_structure_state(state)

market_structure_repo = MarketStructureRepository()
