import logging
from typing import Optional
import psycopg

from backend.app.services.supabase_service import get_supabase_client, get_postgres_connection
from backend.app.core.constants import canonicalize_symbol

logger = logging.getLogger(__name__)

# In-memory stores for test isolation guardrails
# Current State: (source_id, symbol_canonical, timeframe) -> state_dict
_memory_current_state: dict[tuple[str, str, str], dict] = {}

# State History: list of history dicts
_memory_state_history: list[dict] = []

# Processed Event Ledger: set of (source_id, symbol_canonical, timeframe, event_key)
_memory_processed_events: set[tuple[str, str, str, str]] = set()


def clear_state_memory():
    """Helper for test cleanup to guarantee 100% clean test isolation."""
    global _memory_current_state, _memory_state_history, _memory_processed_events
    _memory_current_state.clear()
    _memory_state_history.clear()
    _memory_processed_events.clear()


class MarketStructureStateRepository:
    """
    Authoritative persistence layer for Market Structure State (Step 3).
    Guarantees atomic persistence between current state, transition history,
    and the durable processed-event ledger across both PostgreSQL transactions
    and in-memory test snapshots.
    """

    def get_current_state(
        self,
        source_id: str,
        symbol: str,
        timeframe: str
    ) -> Optional[dict]:
        sym_canon = canonicalize_symbol(symbol)
        key = (source_id, sym_canon, timeframe)

        if key in _memory_current_state:
            return dict(_memory_current_state[key])

        conn = get_postgres_connection()
        if conn:
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT id, source_id, symbol, timeframe, state, previous_state,
                               structure, last_event, last_event_time, last_event_price,
                               state_changed, state_reason, structure_strength, source_event_id,
                               bar_time, bar_index, created_at, updated_at
                        FROM market_structure_state_current
                        WHERE source_id = %s AND UPPER(symbol) = %s AND timeframe = %s;
                    """, (source_id, sym_canon, timeframe))
                    row = cur.fetchone()
                    if row:
                        return {
                            "id": row[0],
                            "source_id": row[1],
                            "symbol": row[2],
                            "timeframe": row[3],
                            "state": row[4],
                            "previous_state": row[5],
                            "structure": row[6],
                            "last_event": row[7],
                            "last_event_time": int(row[8]),
                            "last_event_price": float(row[9]),
                            "state_changed": bool(row[10]),
                            "state_reason": row[11],
                            "structure_strength": row[12],
                            "source_event_id": row[13],
                            "bar_time": int(row[14]),
                            "bar_index": int(row[15]),
                            "created_at": str(row[16]) if row[16] else None,
                            "updated_at": str(row[17]) if row[17] else None,
                        }
            except Exception as ex:
                logger.error(f"[MarketStructureStateRepo] Error fetching current state: {ex}")
            finally:
                conn.close()

        client = get_supabase_client()
        if client:
            try:
                res = client.table("market_structure_state_current")\
                    .select("*")\
                    .eq("source_id", source_id)\
                    .ilike("symbol", sym_canon)\
                    .eq("timeframe", timeframe)\
                    .limit(1)\
                    .execute()
                if res.data:
                    return res.data[0]
            except Exception as ex:
                logger.error(f"[MarketStructureStateRepo] Supabase error fetching current state: {ex}")

        return None

    def is_event_processed(
        self,
        source_id: str,
        symbol: str,
        timeframe: str,
        event_key: str
    ) -> bool:
        sym_canon = canonicalize_symbol(symbol)
        key = (source_id, sym_canon, timeframe, event_key)
        if key in _memory_processed_events:
            return True

        conn = get_postgres_connection()
        if conn:
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT 1 FROM market_structure_state_events_processed
                        WHERE source_id = %s AND UPPER(symbol) = %s AND timeframe = %s AND event_key = %s;
                    """, (source_id, sym_canon, timeframe, event_key))
                    if cur.fetchone():
                        return True
            except Exception as ex:
                logger.error(f"[MarketStructureStateRepo] Error checking processed event: {ex}")
            finally:
                conn.close()

        return False

    def save_state_and_history_if_changed(
        self,
        state: dict,
        event_key: Optional[str] = None
    ) -> bool:
        """
        Atomically:
        1. Upserts market_structure_state_current
        2. If state_changed == True: inserts record into market_structure_state_history
        3. If event_key provided: inserts record into market_structure_state_events_processed
        """
        sym_canon = canonicalize_symbol(state["symbol"])
        source_id = state["source_id"]
        timeframe = state["timeframe"]
        state_changed = bool(state.get("state_changed", False))

        # 1. Update in-memory stores
        curr_key = (source_id, sym_canon, timeframe)
        state_copy = dict(state)
        state_copy["symbol"] = sym_canon
        _memory_current_state[curr_key] = state_copy

        if event_key:
            _memory_processed_events.add((source_id, sym_canon, timeframe, event_key))

        if state_changed:
            history_entry = {
                "source_id": source_id,
                "symbol": sym_canon,
                "timeframe": timeframe,
                "previous_state": state["previous_state"],
                "new_state": state["state"],
                "structure": state["structure"],
                "last_event": state["last_event"],
                "event_time": state["last_event_time"],
                "event_price": state["last_event_price"],
                "state_reason": state["state_reason"],
                "structure_strength": state["structure_strength"],
                "source_event_id": state.get("source_event_id"),
                "bar_time": state["bar_time"],
                "bar_index": state["bar_index"],
            }
            _memory_state_history.append(history_entry)

        # 2. Update PostgreSQL database atomically
        conn = get_postgres_connection()
        if conn:
            try:
                with conn.transaction():
                    with conn.cursor() as cur:
                        # Upsert current state
                        cur.execute("""
                            INSERT INTO market_structure_state_current (
                                source_id, symbol, timeframe, state, previous_state,
                                structure, last_event, last_event_time, last_event_price,
                                state_changed, state_reason, structure_strength, source_event_id,
                                bar_time, bar_index, updated_at
                            ) VALUES (
                                %(source_id)s, %(symbol)s, %(timeframe)s, %(state)s, %(previous_state)s,
                                %(structure)s, %(last_event)s, %(last_event_time)s, %(last_event_price)s,
                                %(state_changed)s, %(state_reason)s, %(structure_strength)s, %(source_event_id)s,
                                %(bar_time)s, %(bar_index)s, now()
                            )
                            ON CONFLICT (source_id, symbol, timeframe)
                            DO UPDATE SET
                                state = EXCLUDED.state,
                                previous_state = EXCLUDED.previous_state,
                                structure = EXCLUDED.structure,
                                last_event = EXCLUDED.last_event,
                                last_event_time = EXCLUDED.last_event_time,
                                last_event_price = EXCLUDED.last_event_price,
                                state_changed = EXCLUDED.state_changed,
                                state_reason = EXCLUDED.state_reason,
                                structure_strength = EXCLUDED.structure_strength,
                                source_event_id = EXCLUDED.source_event_id,
                                bar_time = EXCLUDED.bar_time,
                                bar_index = EXCLUDED.bar_index,
                                updated_at = now();
                        """, state_copy)

                        # Insert history row ONLY if state_changed == True
                        if state_changed:
                            cur.execute("""
                                INSERT INTO market_structure_state_history (
                                    source_id, symbol, timeframe, previous_state, new_state,
                                    structure, last_event, event_time, event_price,
                                    state_reason, structure_strength, source_event_id,
                                    bar_time, bar_index
                                ) VALUES (
                                    %(source_id)s, %(symbol)s, %(timeframe)s, %(previous_state)s, %(new_state)s,
                                    %(structure)s, %(last_event)s, %(event_time)s, %(event_price)s,
                                    %(state_reason)s, %(structure_strength)s, %(source_event_id)s,
                                    %(bar_time)s, %(bar_index)s
                                )
                                ON CONFLICT (source_id, symbol, timeframe, bar_time, new_state)
                                DO NOTHING;
                            """, history_entry)

                        # Mark event as processed in ledger
                        if event_key:
                            cur.execute("""
                                INSERT INTO market_structure_state_events_processed (
                                    source_id, symbol, timeframe, event_key
                                ) VALUES (
                                    %s, %s, %s, %s
                                )
                                ON CONFLICT (source_id, symbol, timeframe, event_key)
                                DO NOTHING;
                            """, (source_id, sym_canon, timeframe, event_key))

                conn.close()
                return True
            except Exception as ex:
                logger.error(f"[MarketStructureStateRepo] Error in atomic state persistence: {ex}")
                if conn:
                    conn.close()
                return False

        # Fallback Supabase REST
        client = get_supabase_client()
        if client:
            try:
                client.table("market_structure_state_current").upsert(state_copy).execute()
                if state_changed:
                    client.table("market_structure_state_history").insert(history_entry).execute()
                if event_key:
                    client.table("market_structure_state_events_processed").upsert({
                        "source_id": source_id,
                        "symbol": sym_canon,
                        "timeframe": timeframe,
                        "event_key": event_key
                    }).execute()
                return True
            except Exception as ex:
                logger.error(f"[MarketStructureStateRepo] Supabase error in state persistence: {ex}")

        return True

    def batch_save_rebuild_state(
        self,
        final_state: dict,
        history_entries: list[dict],
        processed_event_keys: list[str]
    ) -> bool:
        """
        Atomically replaces market structure state during a Full Rebuild:
        1. Updates in-memory stores for clean test isolation.
        2. In 1 single PostgreSQL transaction:
           - Deletes previous history and processed events for (source_id, symbol, timeframe).
           - Upserts final_state into market_structure_state_current.
           - Batch-inserts history_entries using executemany into market_structure_state_history.
           - Batch-inserts processed_event_keys using executemany into market_structure_state_events_processed.
        """
        sym_canon = canonicalize_symbol(final_state["symbol"])
        source_id = final_state["source_id"]
        timeframe = final_state["timeframe"]

        state_copy = dict(final_state)
        state_copy["symbol"] = sym_canon

        # 1. Update in-memory stores
        global _memory_current_state, _memory_state_history, _memory_processed_events
        curr_key = (source_id, sym_canon, timeframe)
        _memory_current_state[curr_key] = state_copy

        # Purge existing history & processed keys for this dataset in memory
        _memory_state_history = [
            h for h in _memory_state_history
            if not (h["source_id"] == source_id and h["symbol"] == sym_canon and h["timeframe"] == timeframe)
        ]
        _memory_state_history.extend(history_entries)

        _memory_processed_events = {
            k for k in _memory_processed_events
            if not (k[0] == source_id and k[1] == sym_canon and k[2] == timeframe)
        }
        for k in processed_event_keys:
            _memory_processed_events.add((source_id, sym_canon, timeframe, k))

        # 2. Direct PostgreSQL transaction
        conn = get_postgres_connection()
        if conn:
            try:
                with conn.transaction():
                    with conn.cursor() as cur:
                        # Clear old history and processed events for clean replacement
                        cur.execute("""
                            DELETE FROM market_structure_state_history
                            WHERE source_id = %s AND UPPER(symbol) = %s AND timeframe = %s;
                        """, (source_id, sym_canon, timeframe))
                        cur.execute("""
                            DELETE FROM market_structure_state_events_processed
                            WHERE source_id = %s AND UPPER(symbol) = %s AND timeframe = %s;
                        """, (source_id, sym_canon, timeframe))

                        # Upsert current state
                        cur.execute("""
                            INSERT INTO market_structure_state_current (
                                source_id, symbol, timeframe, state, previous_state,
                                structure, last_event, last_event_time, last_event_price,
                                state_changed, state_reason, structure_strength, source_event_id,
                                bar_time, bar_index, updated_at
                            ) VALUES (
                                %(source_id)s, %(symbol)s, %(timeframe)s, %(state)s, %(previous_state)s,
                                %(structure)s, %(last_event)s, %(last_event_time)s, %(last_event_price)s,
                                %(state_changed)s, %(state_reason)s, %(structure_strength)s, %(source_event_id)s,
                                %(bar_time)s, %(bar_index)s, now()
                            )
                            ON CONFLICT (source_id, symbol, timeframe)
                            DO UPDATE SET
                                state = EXCLUDED.state,
                                previous_state = EXCLUDED.previous_state,
                                structure = EXCLUDED.structure,
                                last_event = EXCLUDED.last_event,
                                last_event_time = EXCLUDED.last_event_time,
                                last_event_price = EXCLUDED.last_event_price,
                                state_changed = EXCLUDED.state_changed,
                                state_reason = EXCLUDED.state_reason,
                                structure_strength = EXCLUDED.structure_strength,
                                source_event_id = EXCLUDED.source_event_id,
                                bar_time = EXCLUDED.bar_time,
                                bar_index = EXCLUDED.bar_index,
                                updated_at = now();
                        """, state_copy)

                        # Batch insert history
                        if history_entries:
                            cur.executemany("""
                                INSERT INTO market_structure_state_history (
                                    source_id, symbol, timeframe, previous_state, new_state,
                                    structure, last_event, event_time, event_price,
                                    state_reason, structure_strength, source_event_id,
                                    bar_time, bar_index
                                ) VALUES (
                                    %(source_id)s, %(symbol)s, %(timeframe)s, %(previous_state)s, %(new_state)s,
                                    %(structure)s, %(last_event)s, %(event_time)s, %(event_price)s,
                                    %(state_reason)s, %(structure_strength)s, %(source_event_id)s,
                                    %(bar_time)s, %(bar_index)s
                                )
                                ON CONFLICT (source_id, symbol, timeframe, bar_time, new_state)
                                DO NOTHING;
                            """, history_entries)

                        # Batch insert processed events
                        if processed_event_keys:
                            key_tuples = [(source_id, sym_canon, timeframe, k) for k in processed_event_keys]
                            cur.executemany("""
                                INSERT INTO market_structure_state_events_processed (
                                    source_id, symbol, timeframe, event_key
                                ) VALUES (%s, %s, %s, %s)
                                ON CONFLICT (source_id, symbol, timeframe, event_key)
                                DO NOTHING;
                            """, key_tuples)

                conn.close()
                return True
            except Exception as ex:
                logger.error(f"[MarketStructureStateRepo] Error in batch rebuild state persistence: {ex}")
                if conn:
                    conn.close()
                return False

        # Fallback Supabase REST
        client = get_supabase_client()
        if client:
            try:
                client.table("market_structure_state_current").upsert(state_copy).execute()
                if history_entries:
                    client.table("market_structure_state_history").insert(history_entries).execute()
                if processed_event_keys:
                    records = [
                        {
                            "source_id": source_id,
                            "symbol": sym_canon,
                            "timeframe": timeframe,
                            "event_key": k
                        }
                        for k in processed_event_keys
                    ]
                    client.table("market_structure_state_events_processed").upsert(records).execute()
                return True
            except Exception as ex:
                logger.error(f"[MarketStructureStateRepo] Supabase error in batch rebuild persistence: {ex}")

        return True

    def get_state_history(
        self,
        source_id: str,
        symbol: str,
        timeframe: str,
        limit: int = 50
    ) -> list[dict]:
        sym_canon = canonicalize_symbol(symbol)
        mem_filtered = [
            h for h in _memory_state_history
            if h["source_id"] == source_id and h["symbol"] == sym_canon and h["timeframe"] == timeframe
        ]
        if mem_filtered:
            return sorted(mem_filtered, key=lambda x: int(x.get("bar_time", 0)), reverse=True)[:limit]

        conn = get_postgres_connection()
        if conn:
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT id, source_id, symbol, timeframe, previous_state, new_state,
                               structure, last_event, event_time, event_price,
                               state_reason, structure_strength, source_event_id,
                               bar_time, bar_index, created_at
                        FROM market_structure_state_history
                        WHERE source_id = %s AND UPPER(symbol) = %s AND timeframe = %s
                        ORDER BY bar_time DESC
                        LIMIT %s;
                    """, (source_id, sym_canon, timeframe, limit))
                    rows = cur.fetchall()
                    return [
                        {
                            "id": r[0],
                            "source_id": r[1],
                            "symbol": r[2],
                            "timeframe": r[3],
                            "previous_state": r[4],
                            "new_state": r[5],
                            "structure": r[6],
                            "last_event": r[7],
                            "event_time": int(r[8]),
                            "event_price": float(r[9]),
                            "state_reason": r[10],
                            "structure_strength": r[11],
                            "source_event_id": r[12],
                            "bar_time": int(r[13]),
                            "bar_index": int(r[14]),
                            "created_at": str(r[15]) if r[15] else None,
                        }
                        for r in rows
                    ]
            except Exception as ex:
                logger.error(f"[MarketStructureStateRepo] Error fetching state history: {ex}")
            finally:
                conn.close()

        return []

    def clear_state_for_timeframe(
        self,
        source_id: str,
        symbol: str,
        timeframe: str
    ) -> bool:
        """
        Clears current state, history, and processed events for a specific symbol/timeframe.
        Used during Full Rebuild to ensure clean isolation.
        """
        sym_canon = canonicalize_symbol(symbol)
        global _memory_current_state, _memory_state_history, _memory_processed_events
        _memory_current_state.pop((source_id, sym_canon, timeframe), None)
        _memory_state_history = [
            h for h in _memory_state_history
            if not (h["source_id"] == source_id and h["symbol"] == sym_canon and h["timeframe"] == timeframe)
        ]
        _memory_processed_events = {
            k for k in _memory_processed_events
            if not (k[0] == source_id and k[1] == sym_canon and k[2] == timeframe)
        }

        conn = get_postgres_connection()
        if conn:
            try:
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute("""
                            DELETE FROM market_structure_state_current
                            WHERE source_id = %s AND UPPER(symbol) = %s AND timeframe = %s;
                        """, (source_id, sym_canon, timeframe))
                        cur.execute("""
                            DELETE FROM market_structure_state_history
                            WHERE source_id = %s AND UPPER(symbol) = %s AND timeframe = %s;
                        """, (source_id, sym_canon, timeframe))
                        cur.execute("""
                            DELETE FROM market_structure_state_events_processed
                            WHERE source_id = %s AND UPPER(symbol) = %s AND timeframe = %s;
                        """, (source_id, sym_canon, timeframe))
                conn.close()
                return True
            except Exception as ex:
                logger.error(f"[MarketStructureStateRepo] Error clearing state in postgres: {ex}")
                if conn:
                    conn.close()

        return True


market_structure_state_repo = MarketStructureStateRepository()
