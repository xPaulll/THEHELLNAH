import logging
from typing import Optional
from backend.app.repositories.candle_repo import candle_repo
from backend.app.repositories.swing_repo import swing_repo
from backend.app.features.swing_detector import (
    swing_detector,
    get_symbol_point,
    get_symbol_tolerance_points
)
from backend.app.core.constants import (
    SwingType,
    CanonicalStructureEventType,
    canonicalize_symbol
)

from backend.app.repositories.market_structure_repo import market_structure_repo
from backend.app.features.market_structure_detector import (
    market_structure_detector,
    get_break_tolerance_price,
    create_initial_state
)

from backend.app.models.market_structure_state_models import CanonicalStructureEvent
from backend.app.features.market_structure_state_engine import (
    market_structure_state_engine,
    create_initial_market_state
)
from backend.app.repositories.market_structure_state_repo import market_structure_state_repo

logger = logging.getLogger(__name__)

class MarketDataService:
    """
    Coordinates Feature Engine processing between Candle Repository,
    Swing Detector, Swing Repository, Market Structure Detector,
    and Market Structure Repository.
    Supports:
    - Mode A: Full Scan / Rebuild (for initial sync, gap backfill, recovery).
    - Mode B: Incremental Sliding Window (for live candle closed events).
    """

    def recalculate_swings_full(
        self,
        source_id: str,
        symbol: str,
        timeframe: str,
        fractal_n: int = 2
    ) -> int:
        """
        Mode A: Full Scan for Swings (Step 1).
        Loads all historical closed candles in ascending order, detects all swings,
        classifies them, and saves them idempotently to market_swings.
        """
        candles = candle_repo.get_candles_ascending(
            source_id=source_id,
            symbol=symbol,
            timeframe=timeframe,
            limit=10000
        )
        if len(candles) < (2 * fractal_n + 1):
            logger.debug(f"[MarketDataService] Not enough candles ({len(candles)}) for N={fractal_n}")
            return 0

        swings = swing_detector.detect_swings_from_candles(
            candles=candles,
            fractal_n=fractal_n,
            symbol=symbol,
            source_id=source_id,
            timeframe=timeframe
        )

        if swings:
            inserted = swing_repo.upsert_swings(swings)
            logger.info(f"[MarketDataService] Full scan processed {len(swings)} swings for {symbol} ({timeframe})")
            return inserted

        return 0

    def process_live_candle_swing(
        self,
        source_id: str,
        symbol: str,
        timeframe: str,
        fractal_n: int = 2
    ) -> int:
        """
        Mode B: Incremental Sliding Window for Swings (Step 1).
        Called when a single new live candle closes.
        Loads only the minimum safe lookback window (2N + 1 bars) required to evaluate
        the candidate at index M - N, retrieves the latest same-side swing from the
        database/repository, classifies the swing if confirmed, and persists it.
        O(1) complexity relative to total history.
        """
        # Minimum window needed: 2N + 1 candles (load 2N + 5 for safety)
        window_size = 2 * fractal_n + 5
        candles = candle_repo.get_recent_candles_ascending(
            source_id=source_id,
            symbol=symbol,
            timeframe=timeframe,
            limit=window_size
        )
        if len(candles) < (2 * fractal_n + 1):
            return 0

        cand_idx = len(candles) - 1 - fractal_n
        if cand_idx < fractal_n:
            return 0

        is_high, is_low = swing_detector.evaluate_candidate_at_index(candles, cand_idx, fractal_n)
        if not is_high and not is_low:
            return 0

        candidate_candle = candles[cand_idx]
        confirm_candle = candles[cand_idx + fractal_n]
        candidate_epoch = int(candidate_candle["candle_time_epoch"])
        confirm_epoch = int(confirm_candle["candle_time_epoch"])

        point = get_symbol_point(symbol, source_id)
        tol_points = get_symbol_tolerance_points(symbol)
        threshold_price = tol_points * point

        new_swings: list[dict] = []

        if is_high:
            cand_high_price = float(candidate_candle["high"])
            latest_high = swing_repo.get_latest_swing(
                source_id, symbol, timeframe, SwingType.HIGH.value, before_epoch=candidate_epoch
            )
            prev_high_price = float(latest_high["swing_price"]) if latest_high else None
            classification_high = swing_detector.classify_swing_high(cand_high_price, prev_high_price, threshold_price)
            new_swings.append({
                "source_id": source_id,
                "symbol": symbol,
                "timeframe": timeframe,
                "swing_type": SwingType.HIGH.value,
                "swing_candle_time_epoch": candidate_epoch,
                "swing_price": round(cand_high_price, 6),
                "confirmed_at_candle_time_epoch": confirm_epoch,
                "classification": classification_high,
                "fractal_n": fractal_n
            })

        if is_low:
            cand_low_price = float(candidate_candle["low"])
            latest_low = swing_repo.get_latest_swing(
                source_id, symbol, timeframe, SwingType.LOW.value, before_epoch=candidate_epoch
            )
            prev_low_price = float(latest_low["swing_price"]) if latest_low else None
            classification_low = swing_detector.classify_swing_low(cand_low_price, prev_low_price, threshold_price)
            new_swings.append({
                "source_id": source_id,
                "symbol": symbol,
                "timeframe": timeframe,
                "swing_type": SwingType.LOW.value,
                "swing_candle_time_epoch": candidate_epoch,
                "swing_price": round(cand_low_price, 6),
                "confirmed_at_candle_time_epoch": confirm_epoch,
                "classification": classification_low,
                "fractal_n": fractal_n
            })

        if new_swings:
            inserted = swing_repo.upsert_swings(new_swings)
            logger.info(f"[MarketDataService] Incremental swing detected at epoch {candidate_epoch} for {symbol} ({timeframe})")
            return inserted

        return 0

    def recalculate_market_structure_full(
        self,
        source_id: str,
        symbol: str,
        timeframe: str,
        fractal_n: int = 2
    ) -> int:
        """
        Mode A: Full Rebuild for Market Structure (Step 2).
        1. Starts from fresh initial neutral state.
        2. Loads all historical closed candles ascending.
        3. Loads all confirmed market_swings ascending.
        4. For each candle C_t:
           - Applies newly confirmed swings (confirmed_at <= t).
           - Checks MSS or invalidation.
           - Evaluates closed candle close against state (DOUBLE_BREAK, CHOCH, BOS).
        5. Persists events and state idempotently.
        Returns number of structure events generated.
        """
        candles = candle_repo.get_candles_ascending(
            source_id=source_id,
            symbol=symbol,
            timeframe=timeframe,
            limit=10000
        )
        if not candles:
            return 0

        # Load all confirmed swings from Step 1 (single source of truth)
        all_swings = swing_repo.get_confirmed_swings_ascending(
            source_id=source_id,
            symbol=symbol,
            timeframe=timeframe,
            limit=10000
        )

        # Clear stale structure events and state for this dataset before full rebuild
        market_structure_repo.clear_structure_for_timeframe(source_id, symbol, timeframe)
        market_structure_state_repo.clear_state_for_timeframe(source_id, symbol, timeframe)

        state = create_initial_state(source_id, symbol, timeframe)
        step3_state = create_initial_market_state(source_id, symbol, timeframe)
        threshold_price = get_break_tolerance_price(symbol, source_id)

        events: list[dict] = []
        step3_history: list[dict] = []
        processed_keys: set[str] = set()

        sym_canon = canonicalize_symbol(symbol)

        for idx, c in enumerate(candles):
            c_epoch = int(c["candle_time_epoch"])
            bar_idx = idx + 1

            # Find newly confirmed swings available at this candle:
            # confirmed_at > last_processed_confirmation_time AND confirmed_at <= c_epoch
            new_swings = [
                s for s in all_swings
                if int(s.get("confirmed_at_candle_time_epoch", 0)) > state["last_processed_confirmation_time"]
                and int(s.get("confirmed_at_candle_time_epoch", 0)) <= c_epoch
            ]

            # Swings available up to c_epoch
            swings_up_to_c = [
                s for s in all_swings
                if int(s.get("confirmed_at_candle_time_epoch", 0)) <= c_epoch
            ]

            # Step A: Apply newly confirmed swings to state
            state, mss_ev = market_structure_detector.apply_confirmed_swings_to_state(
                state=state,
                new_swings=new_swings,
                all_confirmed_swings_history=swings_up_to_c
            )
            if mss_ev:
                events.append(mss_ev)

            # Step B: Evaluate candle close against state
            state, candle_ev = market_structure_detector.evaluate_closed_candle(
                candle=c,
                state=state,
                threshold_price=threshold_price,
                fractal_n=fractal_n
            )
            chosen_ev = None
            if candle_ev:
                # Priority: If MSS was confirmed on this exact candle, MSS takes priority over CHOCH/BOS
                if not mss_ev:
                    events.append(candle_ev)
                    chosen_ev = candle_ev
                else:
                    chosen_ev = mss_ev
            elif mss_ev:
                chosen_ev = mss_ev

            # Step C: Feed into Step 3 in-memory with deterministic canonical keys
            for sw in new_swings:
                cls = sw.get("classification")
                if cls in [
                    CanonicalStructureEventType.HH.value,
                    CanonicalStructureEventType.HL.value,
                    CanonicalStructureEventType.LH.value,
                    CanonicalStructureEventType.LL.value
                ]:
                    sw_epoch = int(sw.get("swing_candle_time_epoch", c_epoch))
                    sw_type = sw.get("swing_type", "SW")
                    ev_key = f"{source_id}:{sym_canon}:{timeframe}:SWING:{cls}:{sw_epoch}:{sw_type}"
                    is_dup = ev_key in processed_keys
                    processed_keys.add(ev_key)

                    canon_ev = CanonicalStructureEvent(
                        source_id=source_id,
                        symbol=symbol,
                        timeframe=timeframe,
                        event_type=CanonicalStructureEventType(cls),
                        event_time=int(sw.get("confirmed_at_candle_time_epoch", c_epoch)),
                        event_price=float(sw.get("swing_price", 0.0)),
                        bar_time=c_epoch,
                        bar_index=bar_idx,
                        source_event_id=sw.get("id"),
                        is_closed_bar=True,
                        event_key=ev_key,
                        metadata={"swing_type": sw.get("swing_type"), "swing_candle_time_epoch": sw.get("swing_candle_time_epoch")}
                    )
                    step3_state, mutated, _ = market_structure_state_engine.evaluate_structure_event(
                        step3_state, canon_ev, swings_up_to_c, is_duplicate=is_dup
                    )
                    if (mutated or not is_dup) and step3_state.get("state_changed"):
                        step3_history.append({
                            "source_id": source_id,
                            "symbol": sym_canon,
                            "timeframe": timeframe,
                            "previous_state": step3_state["previous_state"],
                            "new_state": step3_state["state"],
                            "structure": step3_state["structure"],
                            "last_event": step3_state["last_event"],
                            "event_time": step3_state["last_event_time"],
                            "event_price": step3_state["last_event_price"],
                            "state_reason": step3_state["state_reason"],
                            "structure_strength": step3_state["structure_strength"],
                            "source_event_id": step3_state.get("source_event_id"),
                            "bar_time": step3_state["bar_time"],
                            "bar_index": step3_state["bar_index"],
                        })

            if chosen_ev:
                ev_type_str = chosen_ev.get("event_type")
                if ev_type_str in CanonicalStructureEventType._value2member_map_:
                    ev_epoch = int(chosen_ev.get("event_candle_time_epoch", c_epoch))
                    ev_price = float(chosen_ev.get("broken_level_price", chosen_ev.get("candle_close", 0.0)))
                    ev_key = f"{source_id}:{sym_canon}:{timeframe}:BREAK:{ev_type_str}:{ev_epoch}:{ev_price:.5f}"
                    is_dup = ev_key in processed_keys
                    processed_keys.add(ev_key)

                    canon_ev = CanonicalStructureEvent(
                        source_id=source_id,
                        symbol=symbol,
                        timeframe=timeframe,
                        event_type=CanonicalStructureEventType(ev_type_str),
                        event_time=ev_epoch,
                        event_price=float(chosen_ev.get("candle_close", 0.0)),
                        bar_time=c_epoch,
                        bar_index=bar_idx,
                        source_event_id=chosen_ev.get("id"),
                        is_closed_bar=True,
                        event_key=ev_key,
                        metadata=chosen_ev.get("decision_context", {})
                    )
                    step3_state, mutated, _ = market_structure_state_engine.evaluate_structure_event(
                        step3_state, canon_ev, swings_up_to_c, is_duplicate=is_dup
                    )
                    if (mutated or not is_dup) and step3_state.get("state_changed"):
                        step3_history.append({
                            "source_id": source_id,
                            "symbol": sym_canon,
                            "timeframe": timeframe,
                            "previous_state": step3_state["previous_state"],
                            "new_state": step3_state["state"],
                            "structure": step3_state["structure"],
                            "last_event": step3_state["last_event"],
                            "event_time": step3_state["last_event_time"],
                            "event_price": step3_state["last_event_price"],
                            "state_reason": step3_state["state_reason"],
                            "structure_strength": step3_state["structure_strength"],
                            "source_event_id": step3_state.get("source_event_id"),
                            "bar_time": step3_state["bar_time"],
                            "bar_index": step3_state["bar_index"],
                        })

        # Persist Step 2
        market_structure_repo.clear_structure_for_timeframe(source_id, symbol, timeframe)
        if events:
            market_structure_repo.upsert_structure_events(events)
        market_structure_repo.upsert_structure_state(state)

        # Batch persist Step 3 atomically in 1 transaction
        market_structure_state_repo.batch_save_rebuild_state(
            final_state=step3_state,
            history_entries=step3_history,
            processed_event_keys=list(processed_keys)
        )

        logger.info(f"[MarketDataService] Full rebuild generated {len(events)} structure events for {symbol} ({timeframe})")
        return len(events)

    def _pipe_to_step3(
        self,
        source_id: str,
        symbol: str,
        timeframe: str,
        c_epoch: int,
        bar_index: int,
        new_swings: list[dict],
        chosen_ev: Optional[dict],
        swings_up_to_c: list[dict],
        current_step3_state: dict
    ) -> dict:
        """
        Feeds newly available Step 1 swings and Step 2 structural events
        into Step 3 Market Structure State Engine in strict chronological priority:
        1. Confirmed Swings (HH, HL, LH, LL)
        2. Break/Shift Events (CHOCH, MSS, BOS, DOUBLE_BREAK)
        Persists state and history atomically for live incremental transitions.
        """
        st3 = dict(current_step3_state)
        sym_canon = canonicalize_symbol(symbol)

        # 1. Process confirmed swings
        for sw in new_swings:
            cls = sw.get("classification")
            if cls in [
                CanonicalStructureEventType.HH.value,
                CanonicalStructureEventType.HL.value,
                CanonicalStructureEventType.LH.value,
                CanonicalStructureEventType.LL.value
            ]:
                sw_epoch = int(sw.get("swing_candle_time_epoch", c_epoch))
                sw_type = sw.get("swing_type", "SW")
                ev_key = f"{source_id}:{sym_canon}:{timeframe}:SWING:{cls}:{sw_epoch}:{sw_type}"
                is_dup = market_structure_state_repo.is_event_processed(source_id, symbol, timeframe, ev_key)
                canon_ev = CanonicalStructureEvent(
                    source_id=source_id,
                    symbol=symbol,
                    timeframe=timeframe,
                    event_type=CanonicalStructureEventType(cls),
                    event_time=int(sw.get("confirmed_at_candle_time_epoch", c_epoch)),
                    event_price=float(sw.get("swing_price", 0.0)),
                    bar_time=c_epoch,
                    bar_index=bar_index,
                    source_event_id=sw.get("id"),
                    is_closed_bar=True,
                    event_key=ev_key,
                    metadata={"swing_type": sw.get("swing_type"), "swing_candle_time_epoch": sw.get("swing_candle_time_epoch")}
                )
                st3, mutated, _ = market_structure_state_engine.evaluate_structure_event(
                    st3, canon_ev, swings_up_to_c, is_duplicate=is_dup
                )
                if mutated or not is_dup:
                    market_structure_state_repo.save_state_and_history_if_changed(st3, event_key=ev_key)

        # 2. Process Step 2 structural break/shift events
        if chosen_ev:
            ev_type_str = chosen_ev.get("event_type")
            if ev_type_str in CanonicalStructureEventType._value2member_map_:
                ev_epoch = int(chosen_ev.get("event_candle_time_epoch", c_epoch))
                ev_price = float(chosen_ev.get("broken_level_price", chosen_ev.get("candle_close", 0.0)))
                ev_key = f"{source_id}:{sym_canon}:{timeframe}:BREAK:{ev_type_str}:{ev_epoch}:{ev_price:.5f}"
                is_dup = market_structure_state_repo.is_event_processed(source_id, symbol, timeframe, ev_key)
                canon_ev = CanonicalStructureEvent(
                    source_id=source_id,
                    symbol=symbol,
                    timeframe=timeframe,
                    event_type=CanonicalStructureEventType(ev_type_str),
                    event_time=ev_epoch,
                    event_price=float(chosen_ev.get("candle_close", 0.0)),
                    bar_time=c_epoch,
                    bar_index=bar_index,
                    source_event_id=chosen_ev.get("id"),
                    is_closed_bar=True,
                    event_key=ev_key,
                    metadata=chosen_ev.get("decision_context", {})
                )
                st3, mutated, _ = market_structure_state_engine.evaluate_structure_event(
                    st3, canon_ev, swings_up_to_c, is_duplicate=is_dup
                )
                if mutated or not is_dup:
                    market_structure_state_repo.save_state_and_history_if_changed(st3, event_key=ev_key)

        return st3

    def process_live_market_structure(
        self,
        source_id: str,
        symbol: str,
        timeframe: str,
        fractal_n: int = 2
    ) -> list[dict]:
        """
        Mode B: Incremental Market Structure Processing (Step 2 + Step 3).
        Called when a single new live candle closes.
        """
        state = market_structure_repo.get_structure_state(source_id, symbol, timeframe)
        if not state:
            state = create_initial_state(source_id, symbol, timeframe)

        step3_state = market_structure_state_repo.get_current_state(source_id, symbol, timeframe)
        if not step3_state:
            step3_state = create_initial_market_state(source_id, symbol, timeframe)

        last_c_epoch = int(state.get("last_processed_candle_time_epoch", 0))
        recent_candles = candle_repo.get_recent_candles_ascending(
            source_id=source_id,
            symbol=symbol,
            timeframe=timeframe,
            limit=20
        )
        if not recent_candles:
            return []

        new_candles = [c for c in recent_candles if int(c["candle_time_epoch"]) > last_c_epoch]
        if not new_candles:
            return []

        threshold_price = get_break_tolerance_price(symbol, source_id)
        all_confirmed_swings = swing_repo.get_confirmed_swings_ascending(
            source_id=source_id,
            symbol=symbol,
            timeframe=timeframe,
            limit=1000
        )

        emitted_events: list[dict] = []

        for idx, c in enumerate(new_candles):
            c_epoch = int(c["candle_time_epoch"])
            bar_idx = idx + 1

            new_swings = swing_repo.get_newly_confirmed_swings(
                source_id=source_id,
                symbol=symbol,
                timeframe=timeframe,
                after_confirmation_time=int(state.get("last_processed_confirmation_time", 0)),
                up_to_candle_time=c_epoch
            )

            swings_up_to_c = [
                s for s in all_confirmed_swings
                if int(s.get("confirmed_at_candle_time_epoch", 0)) <= c_epoch
            ]

            # Step A: Apply swings
            state, mss_ev = market_structure_detector.apply_confirmed_swings_to_state(
                state=state,
                new_swings=new_swings,
                all_confirmed_swings_history=swings_up_to_c
            )
            if mss_ev:
                emitted_events.append(mss_ev)
                market_structure_repo.save_event_and_state(mss_ev, state)

            # Step B: Evaluate candle close
            state, candle_ev = market_structure_detector.evaluate_closed_candle(
                candle=c,
                state=state,
                threshold_price=threshold_price,
                fractal_n=fractal_n
            )
            chosen_ev = None
            if candle_ev and not mss_ev:
                emitted_events.append(candle_ev)
                market_structure_repo.save_event_and_state(candle_ev, state)
                chosen_ev = candle_ev
            elif mss_ev:
                chosen_ev = mss_ev
            elif not mss_ev:
                market_structure_repo.upsert_structure_state(state)

            # Step C: Pipe into Step 3
            step3_state = self._pipe_to_step3(
                source_id=source_id,
                symbol=symbol,
                timeframe=timeframe,
                c_epoch=c_epoch,
                bar_index=bar_idx,
                new_swings=new_swings,
                chosen_ev=chosen_ev,
                swings_up_to_c=swings_up_to_c,
                current_step3_state=step3_state
            )

        return emitted_events


market_data_service = MarketDataService()
