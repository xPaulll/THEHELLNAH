import logging
import json
from typing import Optional
import psycopg
from psycopg.types.json import Jsonb

from backend.app.services.supabase_service import get_supabase_client, get_postgres_connection
from backend.app.core.constants import canonicalize_symbol

logger = logging.getLogger(__name__)

# In-memory storage for test isolation guardrail:
# Events: (source_id, symbol_canonical, timeframe, epoch, event_type) -> event_dict
_memory_structure_events: dict[tuple[str, str, str, int, str], dict] = {}

# State: (source_id, symbol_canonical, timeframe) -> state_dict
_memory_structure_state: dict[tuple[str, str, str], dict] = {}


def clear_memory_store():
    """Helper for test cleanup to guarantee clean isolation."""
    global _memory_structure_events, _memory_structure_state
    _memory_structure_events.clear()
    _memory_structure_state.clear()


class MarketStructureRepository:
    """
    Authoritative persistence layer for market structure events and state.
    Strictly uses canonical symbols (uppercase).
    Guarantees atomic persistence between events and state across both
    PostgreSQL transactions and in-memory test snapshots.
    """

    def __init__(self):
        # Failure injection flags for atomicity verification
        self._fail_event_write: bool = False
        self._fail_state_write: bool = False

    def clear_structure_for_timeframe(
        self,
        source_id: str,
        symbol: str,
        timeframe: str
    ) -> bool:
        """
        Clears structure events and state for a specific source, symbol, and timeframe.
        Used during Full Rebuild to ensure old stale events do not contaminate freshly rebuilt history.
        """
        sym_canon = canonicalize_symbol(symbol)
        global _memory_structure_events, _memory_structure_state
        keys_to_del = [
            k for k in _memory_structure_events.keys()
            if k[0] == source_id and k[1] == sym_canon and k[2] == timeframe
        ]
        for k in keys_to_del:
            _memory_structure_events.pop(k, None)
        _memory_structure_state.pop((source_id, sym_canon, timeframe), None)

        conn = get_postgres_connection()
        if conn:
            try:
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute("""
                            DELETE FROM market_structure_events
                            WHERE source_id = %s AND UPPER(symbol) = %s AND timeframe = %s;
                        """, (source_id, sym_canon, timeframe))
                        cur.execute("""
                            DELETE FROM market_structure_state
                            WHERE source_id = %s AND UPPER(symbol) = %s AND timeframe = %s;
                        """, (source_id, sym_canon, timeframe))
                conn.close()
                return True
            except Exception as ex:
                logger.error(f"[MarketStructureRepo] Error clearing structure in postgres: {ex}")
                if conn:
                    conn.close()

        client = get_supabase_client()
        if client:
            try:
                client.table("market_structure_events").delete()\
                    .eq("source_id", source_id)\
                    .ilike("symbol", sym_canon)\
                    .eq("timeframe", timeframe)\
                    .execute()
                client.table("market_structure_state").delete()\
                    .eq("source_id", source_id)\
                    .ilike("symbol", sym_canon)\
                    .eq("timeframe", timeframe)\
                    .execute()
            except Exception as ex:
                logger.error(f"[MarketStructureRepo] Error clearing structure in supabase: {ex}")

        return True

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

        conn = get_postgres_connection()
        if conn:
            try:
                with conn.transaction():
                    with conn.cursor() as cur:
                        for ev in events:
                            sym_canon = canonicalize_symbol(ev["symbol"])
                            cur.execute("""
                                INSERT INTO market_structure_events (
                                    source_id, symbol, timeframe, event_type,
                                    event_candle_time_epoch, broken_swing_candle_time_epoch,
                                    broken_swing_type, broken_swing_price,
                                    broken_high_swing_candle_time_epoch, broken_high_swing_price,
                                    broken_low_swing_candle_time_epoch, broken_low_swing_price,
                                    candle_close, break_threshold, previous_bias,
                                    resulting_bias, transition_state, fractal_n, decision_context
                                ) VALUES (
                                    %(source_id)s, %(symbol)s, %(timeframe)s, %(event_type)s,
                                    %(event_candle_time_epoch)s, %(broken_swing_candle_time_epoch)s,
                                    %(broken_swing_type)s, %(broken_swing_price)s,
                                    %(broken_high_swing_candle_time_epoch)s, %(broken_high_swing_price)s,
                                    %(broken_low_swing_candle_time_epoch)s, %(broken_low_swing_price)s,
                                    %(candle_close)s, %(break_threshold)s, %(previous_bias)s,
                                    %(resulting_bias)s, %(transition_state)s, %(fractal_n)s, %(decision_context)s
                                )
                                ON CONFLICT (source_id, symbol, timeframe, event_candle_time_epoch, event_type)
                                DO UPDATE SET
                                    candle_close = EXCLUDED.candle_close,
                                    decision_context = EXCLUDED.decision_context;
                            """, {
                                "source_id": ev["source_id"],
                                "symbol": sym_canon,
                                "timeframe": ev["timeframe"],
                                "event_type": ev["event_type"],
                                "event_candle_time_epoch": ev["event_candle_time_epoch"],
                                "broken_swing_candle_time_epoch": ev.get("broken_swing_candle_time_epoch"),
                                "broken_swing_type": ev.get("broken_swing_type"),
                                "broken_swing_price": ev.get("broken_swing_price"),
                                "broken_high_swing_candle_time_epoch": ev.get("broken_high_swing_candle_time_epoch"),
                                "broken_high_swing_price": ev.get("broken_high_swing_price"),
                                "broken_low_swing_candle_time_epoch": ev.get("broken_low_swing_candle_time_epoch"),
                                "broken_low_swing_price": ev.get("broken_low_swing_price"),
                                "candle_close": ev["candle_close"],
                                "break_threshold": ev.get("break_threshold", 0.0),
                                "previous_bias": ev["previous_bias"],
                                "resulting_bias": ev["resulting_bias"],
                                "transition_state": ev["transition_state"],
                                "fractal_n": ev.get("fractal_n", 2),
                                "decision_context": Jsonb(ev.get("decision_context", {}))
                            })
                conn.close()
                return len(events)
            except Exception as ex:
                logger.error(f"[MarketStructureRepo] Postgres upsert events error: {type(ex).__name__}: {ex}")
                if conn:
                    conn.close()

        client = get_supabase_client()
        if client:
            try:
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
        return self.save_event_and_state(None, state)

    def save_event_and_state(
        self,
        event: Optional[dict],
        state: dict
    ) -> bool:
        """
        True atomic persistence boundary:
        Upserts event (if present) and state in tandem.
        If either fails, the entire transaction is rolled back.
        Guaranteed across both PostgreSQL transactions and in-memory snapshots.
        """
        global _memory_structure_events, _memory_structure_state
        events_snapshot = dict(_memory_structure_events)
        state_snapshot = dict(_memory_structure_state)

        # 1. Check direct PostgreSQL connection
        conn = get_postgres_connection()
        if conn:
            try:
                with conn.transaction():
                    with conn.cursor() as cur:
                        if self._fail_event_write:
                            raise RuntimeError("Simulated event write failure")

                        event_id = None
                        if event:
                            sym_canon = canonicalize_symbol(event["symbol"])
                            cur.execute("""
                                INSERT INTO market_structure_events (
                                    source_id, symbol, timeframe, event_type,
                                    event_candle_time_epoch, broken_swing_candle_time_epoch,
                                    broken_swing_type, broken_swing_price,
                                    broken_high_swing_candle_time_epoch, broken_high_swing_price,
                                    broken_low_swing_candle_time_epoch, broken_low_swing_price,
                                    candle_close, break_threshold, previous_bias,
                                    resulting_bias, transition_state, fractal_n, decision_context
                                ) VALUES (
                                    %(source_id)s, %(symbol)s, %(timeframe)s, %(event_type)s,
                                    %(event_candle_time_epoch)s, %(broken_swing_candle_time_epoch)s,
                                    %(broken_swing_type)s, %(broken_swing_price)s,
                                    %(broken_high_swing_candle_time_epoch)s, %(broken_high_swing_price)s,
                                    %(broken_low_swing_candle_time_epoch)s, %(broken_low_swing_price)s,
                                    %(candle_close)s, %(break_threshold)s, %(previous_bias)s,
                                    %(resulting_bias)s, %(transition_state)s, %(fractal_n)s, %(decision_context)s
                                )
                                ON CONFLICT (source_id, symbol, timeframe, event_candle_time_epoch, event_type)
                                DO UPDATE SET
                                    candle_close = EXCLUDED.candle_close,
                                    decision_context = EXCLUDED.decision_context
                                RETURNING id;
                            """, {
                                "source_id": event["source_id"],
                                "symbol": sym_canon,
                                "timeframe": event["timeframe"],
                                "event_type": event["event_type"],
                                "event_candle_time_epoch": event["event_candle_time_epoch"],
                                "broken_swing_candle_time_epoch": event.get("broken_swing_candle_time_epoch"),
                                "broken_swing_type": event.get("broken_swing_type"),
                                "broken_swing_price": event.get("broken_swing_price"),
                                "broken_high_swing_candle_time_epoch": event.get("broken_high_swing_candle_time_epoch"),
                                "broken_high_swing_price": event.get("broken_high_swing_price"),
                                "broken_low_swing_candle_time_epoch": event.get("broken_low_swing_candle_time_epoch"),
                                "broken_low_swing_price": event.get("broken_low_swing_price"),
                                "candle_close": event["candle_close"],
                                "break_threshold": event.get("break_threshold", 0.0),
                                "previous_bias": event["previous_bias"],
                                "resulting_bias": event["resulting_bias"],
                                "transition_state": event["transition_state"],
                                "fractal_n": event.get("fractal_n", 2),
                                "decision_context": Jsonb(event.get("decision_context", {}))
                            })
                            row = cur.fetchone()
                            if row:
                                event_id = row[0]
                                if not state.get("pending_choch_event_id") and event["event_type"] in ("CHOCH_BULLISH", "CHOCH_BEARISH"):
                                    state["pending_choch_event_id"] = event_id

                        if self._fail_state_write:
                            raise RuntimeError("Simulated state write failure")

                        state_sym = canonicalize_symbol(state["symbol"])
                        cur.execute("""
                            INSERT INTO market_structure_state (
                                source_id, symbol, timeframe, bias, transition_state,
                                last_processed_candle_time_epoch, last_processed_confirmation_time,
                                protected_high_candle_time_epoch, protected_high_price,
                                protected_low_candle_time_epoch, protected_low_price,
                                bullish_break_level_candle_time_epoch, bullish_break_level_price,
                                bearish_break_level_candle_time_epoch, bearish_break_level_price,
                                last_broken_high_candle_time_epoch, last_broken_low_candle_time_epoch,
                                pending_choch_event_id, pending_choch_epoch, pending_choch_continuation_target_price,
                                updated_at
                            ) VALUES (
                                %(source_id)s, %(symbol)s, %(timeframe)s, %(bias)s, %(transition_state)s,
                                %(last_processed_candle_time_epoch)s, %(last_processed_confirmation_time)s,
                                %(protected_high_candle_time_epoch)s, %(protected_high_price)s,
                                %(protected_low_candle_time_epoch)s, %(protected_low_price)s,
                                %(bullish_break_level_candle_time_epoch)s, %(bullish_break_level_price)s,
                                %(bearish_break_level_candle_time_epoch)s, %(bearish_break_level_price)s,
                                %(last_broken_high_candle_time_epoch)s, %(last_broken_low_candle_time_epoch)s,
                                %(pending_choch_event_id)s, %(pending_choch_epoch)s, %(pending_choch_continuation_target_price)s,
                                NOW()
                            )
                            ON CONFLICT (source_id, symbol, timeframe)
                            DO UPDATE SET
                                bias = EXCLUDED.bias,
                                transition_state = EXCLUDED.transition_state,
                                last_processed_candle_time_epoch = EXCLUDED.last_processed_candle_time_epoch,
                                last_processed_confirmation_time = EXCLUDED.last_processed_confirmation_time,
                                protected_high_candle_time_epoch = EXCLUDED.protected_high_candle_time_epoch,
                                protected_high_price = EXCLUDED.protected_high_price,
                                protected_low_candle_time_epoch = EXCLUDED.protected_low_candle_time_epoch,
                                protected_low_price = EXCLUDED.protected_low_price,
                                bullish_break_level_candle_time_epoch = EXCLUDED.bullish_break_level_candle_time_epoch,
                                bullish_break_level_price = EXCLUDED.bullish_break_level_price,
                                bearish_break_level_candle_time_epoch = EXCLUDED.bearish_break_level_candle_time_epoch,
                                bearish_break_level_price = EXCLUDED.bearish_break_level_price,
                                last_broken_high_candle_time_epoch = EXCLUDED.last_broken_high_candle_time_epoch,
                                last_broken_low_candle_time_epoch = EXCLUDED.last_broken_low_candle_time_epoch,
                                pending_choch_event_id = EXCLUDED.pending_choch_event_id,
                                pending_choch_epoch = EXCLUDED.pending_choch_epoch,
                                pending_choch_continuation_target_price = EXCLUDED.pending_choch_continuation_target_price,
                                updated_at = NOW();
                        """, {
                            "source_id": state["source_id"],
                            "symbol": state_sym,
                            "timeframe": state["timeframe"],
                            "bias": state["bias"],
                            "transition_state": state["transition_state"],
                            "last_processed_candle_time_epoch": state["last_processed_candle_time_epoch"],
                            "last_processed_confirmation_time": state.get("last_processed_confirmation_time", 0),
                            "protected_high_candle_time_epoch": state.get("protected_high_candle_time_epoch"),
                            "protected_high_price": state.get("protected_high_price"),
                            "protected_low_candle_time_epoch": state.get("protected_low_candle_time_epoch"),
                            "protected_low_price": state.get("protected_low_price"),
                            "bullish_break_level_candle_time_epoch": state.get("bullish_break_level_candle_time_epoch"),
                            "bullish_break_level_price": state.get("bullish_break_level_price"),
                            "bearish_break_level_candle_time_epoch": state.get("bearish_break_level_candle_time_epoch"),
                            "bearish_break_level_price": state.get("bearish_break_level_price"),
                            "last_broken_high_candle_time_epoch": state.get("last_broken_high_candle_time_epoch"),
                            "last_broken_low_candle_time_epoch": state.get("last_broken_low_candle_time_epoch"),
                            "pending_choch_event_id": state.get("pending_choch_event_id"),
                            "pending_choch_epoch": state.get("pending_choch_epoch"),
                            "pending_choch_continuation_target_price": state.get("pending_choch_continuation_target_price"),
                        })
                conn.close()

                # Sync into memory store on success
                if event:
                    sym_canon = canonicalize_symbol(event["symbol"])
                    ev_copy = dict(event)
                    ev_copy["symbol"] = sym_canon
                    if event_id:
                        ev_copy["id"] = event_id
                    _memory_structure_events[(event["source_id"], sym_canon, event["timeframe"], event["event_candle_time_epoch"], event["event_type"])] = ev_copy
                
                st_copy = dict(state)
                st_copy["symbol"] = state_sym
                _memory_structure_state[(state["source_id"], state_sym, state["timeframe"])] = st_copy
                return True

            except Exception as ex:
                logger.error(f"[MarketStructureRepo] Atomic transaction rolled back: {type(ex).__name__}: {ex}")
                if conn:
                    conn.close()
                # Rollback in-memory state
                _memory_structure_events = events_snapshot
                _memory_structure_state = state_snapshot
                raise ex

        # 2. In-memory atomic handling (e.g. for testing environments or fallback)
        try:
            if self._fail_event_write:
                raise RuntimeError("Simulated event write failure")

            if event:
                sym_canon = canonicalize_symbol(event["symbol"])
                key = (
                    event["source_id"],
                    sym_canon,
                    event["timeframe"],
                    event["event_candle_time_epoch"],
                    event["event_type"]
                )
                ev_copy = dict(event)
                ev_copy["symbol"] = sym_canon
                _memory_structure_events[key] = ev_copy

            if self._fail_state_write:
                raise RuntimeError("Simulated state write failure")

            state_sym = canonicalize_symbol(state["symbol"])
            state_key = (state["source_id"], state_sym, state["timeframe"])
            st_copy = dict(state)
            st_copy["symbol"] = state_sym
            _memory_structure_state[state_key] = st_copy

            # Supabase REST fallback if configured
            client = get_supabase_client()
            if client:
                try:
                    if event:
                        client.table("market_structure_events").upsert(
                            [ev_copy],
                            on_conflict="source_id,symbol,timeframe,event_candle_time_epoch,event_type"
                        ).execute()
                    client.table("market_structure_state").upsert(
                        st_copy,
                        on_conflict="source_id,symbol,timeframe"
                    ).execute()
                except Exception as ex:
                    logger.error(f"[MarketStructureRepo] Supabase fallback error: {ex}")

            return True

        except Exception as ex:
            # Atomic rollback of all memory state to pre-call snapshot
            _memory_structure_events = events_snapshot
            _memory_structure_state = state_snapshot
            raise ex


market_structure_repo = MarketStructureRepository()
