import logging
from typing import Optional
from backend.app.repositories.candle_repo import candle_repo
from backend.app.repositories.swing_repo import swing_repo
from backend.app.features.swing_detector import (
    swing_detector,
    get_symbol_point,
    get_symbol_tolerance_points
)
from backend.app.core.constants import SwingType

from backend.app.repositories.market_structure_repo import market_structure_repo
from backend.app.features.market_structure_detector import (
    market_structure_detector,
    get_break_tolerance_price,
    create_initial_state
)

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

        state = create_initial_state(source_id, symbol, timeframe)
        threshold_price = get_break_tolerance_price(symbol, source_id)

        events: list[dict] = []

        for c in candles:
            c_epoch = int(c["candle_time_epoch"])

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
            if candle_ev:
                # Priority: If MSS was confirmed on this exact candle, MSS takes priority over CHOCH/BOS
                if not mss_ev:
                    events.append(candle_ev)

        # Persist events and state
        if events:
            market_structure_repo.upsert_structure_events(events)
        market_structure_repo.upsert_structure_state(state)

        logger.info(f"[MarketDataService] Full rebuild generated {len(events)} structure events for {symbol} ({timeframe})")
        return len(events)

    def process_live_market_structure(
        self,
        source_id: str,
        symbol: str,
        timeframe: str,
        fractal_n: int = 2
    ) -> list[dict]:
        """
        Mode B: Incremental Market Structure Processing (Step 2).
        Called when a single new live candle closes.
        1. Loads current persisted state from DB (or creates initial state).
        2. Retrieves newly closed candles with epoch > last_processed_candle_time_epoch.
        3. For each new candle:
           - Retrieves newly confirmed swings:
             confirmed_at > last_processed_confirmation_time AND confirmed_at <= c_epoch.
           - Applies swings to state (MSS detection, bootstrap, invalidation).
           - Evaluates candle close (DOUBLE_BREAK, CHOCH, BOS).
           - Persists any emitted event and updates state atomically.
        Returns list of newly emitted events.
        """
        state = market_structure_repo.get_structure_state(source_id, symbol, timeframe)
        if not state:
            state = create_initial_state(source_id, symbol, timeframe)

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

        for c in new_candles:
            c_epoch = int(c["candle_time_epoch"])

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
            if candle_ev and not mss_ev:
                emitted_events.append(candle_ev)
                market_structure_repo.save_event_and_state(candle_ev, state)
            elif not mss_ev:
                market_structure_repo.upsert_structure_state(state)

        return emitted_events

market_data_service = MarketDataService()
