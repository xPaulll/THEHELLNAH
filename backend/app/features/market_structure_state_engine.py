import logging
from typing import Optional
from backend.app.core.constants import (
    MarketState,
    StructureStrength,
    StructureConfiguration,
    CanonicalStructureEventType,
    canonicalize_symbol,
    SwingClassification
)
from backend.app.models.market_structure_state_models import CanonicalStructureEvent

logger = logging.getLogger(__name__)


def create_initial_market_state(source_id: str, symbol: str, timeframe: str) -> dict:
    """
    Creates fresh, deterministic initial market regime state (Step 3).
    Starts strictly at UNKNOWN with INSUFFICIENT strength and NONE configuration.
    """
    return {
        "source_id": source_id,
        "symbol": canonicalize_symbol(symbol),
        "timeframe": timeframe,
        "state": MarketState.UNKNOWN.value,
        "previous_state": MarketState.UNKNOWN.value,
        "structure": StructureConfiguration.NONE.value,
        "last_event": "NONE",
        "last_event_time": 0,
        "last_event_price": 0.0,
        "state_changed": False,
        "state_reason": "Initial state: insufficient structural data",
        "structure_strength": StructureStrength.INSUFFICIENT.value,
        "source_event_id": None,
        "bar_time": 0,
        "bar_index": 0,
    }


def check_ordered_bullish_sequence(recent_swings: list[dict]) -> bool:
    """
    Validates whether recent confirmed swings contain an ordered bullish structural sequence:
    HL -> HH or HH -> HL with ZERO intervening contrary directional swings (LH or LL).
    The sequence must terminate on a bullish swing (HL or HH).
    Prevents false positive bug where HH and HL merely exist somewhere in history.
    """
    dir_swings = [
        s for s in recent_swings
        if s.get("classification") in [
            SwingClassification.HL.value,
            SwingClassification.HH.value,
            SwingClassification.LH.value,
            SwingClassification.LL.value
        ]
    ]
    if len(dir_swings) < 2:
        return False

    last_swing = dir_swings[-1]
    cls_last = last_swing.get("classification")
    if cls_last not in [SwingClassification.HL.value, SwingClassification.HH.value]:
        return False

    target_cls = (
        SwingClassification.HH.value
        if cls_last == SwingClassification.HL.value
        else SwingClassification.HL.value
    )

    # Scan backwards from the swing immediately preceding the last swing
    for i in range(len(dir_swings) - 2, -1, -1):
        cls_i = dir_swings[i].get("classification")
        # Intervening contrary swing breaks the continuous structural path
        if cls_i in [SwingClassification.LH.value, SwingClassification.LL.value]:
            return False
        if cls_i == target_cls:
            return True

    return False


def check_ordered_bearish_sequence(recent_swings: list[dict]) -> bool:
    """
    Validates whether recent confirmed swings contain an ordered bearish structural sequence:
    LH -> LL or LL -> LH with ZERO intervening contrary directional swings (HH or HL).
    The sequence must terminate on a bearish swing (LH or LL).
    Prevents false positive bug where LH and LL merely exist somewhere in history.
    """
    dir_swings = [
        s for s in recent_swings
        if s.get("classification") in [
            SwingClassification.LH.value,
            SwingClassification.LL.value,
            SwingClassification.HH.value,
            SwingClassification.HL.value
        ]
    ]
    if len(dir_swings) < 2:
        return False

    last_swing = dir_swings[-1]
    cls_last = last_swing.get("classification")
    if cls_last not in [SwingClassification.LH.value, SwingClassification.LL.value]:
        return False

    target_cls = (
        SwingClassification.LL.value
        if cls_last == SwingClassification.LH.value
        else SwingClassification.LH.value
    )

    # Scan backwards from the swing immediately preceding the last swing
    for i in range(len(dir_swings) - 2, -1, -1):
        cls_i = dir_swings[i].get("classification")
        # Intervening contrary swing breaks the continuous structural path
        if cls_i in [SwingClassification.HH.value, SwingClassification.HL.value]:
            return False
        if cls_i == target_cls:
            return True

    return False


class MarketStructureStateEngine:
    """
    Pure, deterministic market structure regime state engine (Step 3).
    Strictly consumes validated canonical events.
    Does zero swing detection and zero candle break calculation.
    """

    def evaluate_structure_event(
        self,
        current_state: dict,
        event: CanonicalStructureEvent,
        recent_swings_history: Optional[list[dict]] = None,
        is_duplicate: bool = False
    ) -> tuple[dict, bool, str]:
        """
        Evaluates a single canonical structural event against current state.
        Returns:
            (updated_state_dict, was_mutated: bool, status_message: str)
        """
        swings = recent_swings_history or []

        # 1. Bar 0 / Unclosed Candle Policy Guardrail
        if not event.is_closed_bar or event.bar_index <= 0:
            msg = f"BAR_0_REJECTED: Unclosed or forming candle (bar_index={event.bar_index}) cannot mutate persistent state"
            logger.debug(f"[STRUCTURE_STATE_REJECT] {msg}")
            return dict(current_state), False, msg

        # 2. Idempotency & Duplicate Guardrail
        if is_duplicate:
            msg = f"DUPLICATE_EVENT_IGNORED: {event.event_key} has already been processed"
            logger.debug(f"[STRUCTURE_STATE_REJECT] {msg}")
            return dict(current_state), False, msg

        # 3. Chronological Integrity Guardrail
        curr_bar_time = int(current_state.get("bar_time", 0))
        if event.bar_time < curr_bar_time:
            msg = f"OUT_OF_ORDER_EVENT: event bar_time {event.bar_time} < current state bar_time {curr_bar_time}"
            logger.warning(
                f"[STRUCTURE_STATE_REJECT] symbol={event.symbol} tf={event.timeframe} "
                f"event={event.event_type.value} reason=OUT_OF_ORDER_EVENT"
            )
            return dict(current_state), False, msg

        st = dict(current_state)
        prior_state = st["state"]
        ev_type = event.event_type

        # Base snapshot metadata updates
        st["source_id"] = event.source_id
        st["symbol"] = canonicalize_symbol(event.symbol)
        st["timeframe"] = event.timeframe
        st["last_event"] = ev_type.value
        st["last_event_time"] = event.event_time
        st["last_event_price"] = event.event_price
        st["bar_time"] = event.bar_time
        st["bar_index"] = event.bar_index
        st["source_event_id"] = event.source_event_id

        new_state = prior_state
        config = st.get("structure", StructureConfiguration.NONE.value)
        strength = st.get("structure_strength", StructureStrength.INSUFFICIENT.value)
        reason = ""

        # ====================================================================
        # STATE MACHINE EVALUATION
        # ====================================================================

        if prior_state == MarketState.UNKNOWN.value:
            if check_ordered_bullish_sequence(swings):
                new_state = MarketState.BULLISH.value
                config = StructureConfiguration.HH_HL.value
                strength = StructureStrength.CONFIRMED.value
                reason = "Bullish confirmation established by ordered HH + HL sequence"
            elif check_ordered_bearish_sequence(swings):
                new_state = MarketState.BEARISH.value
                config = StructureConfiguration.LH_LL.value
                strength = StructureStrength.CONFIRMED.value
                reason = "Bearish confirmation established by ordered LH + LL sequence"
            else:
                new_state = MarketState.NEUTRAL.value
                config = StructureConfiguration.NONE.value
                strength = StructureStrength.DEVELOPING.value
                reason = "Structural data detected, awaiting directional sequence confirmation"

        elif prior_state == MarketState.NEUTRAL.value:
            if check_ordered_bullish_sequence(swings):
                new_state = MarketState.BULLISH.value
                config = StructureConfiguration.HH_HL.value
                strength = StructureStrength.CONFIRMED.value
                reason = "Bullish confirmation established by ordered HH + HL sequence"
            elif check_ordered_bearish_sequence(swings):
                new_state = MarketState.BEARISH.value
                config = StructureConfiguration.LH_LL.value
                strength = StructureStrength.CONFIRMED.value
                reason = "Bearish confirmation established by ordered LH + LL sequence"
            else:
                new_state = MarketState.NEUTRAL.value
                config = StructureConfiguration.MIXED.value if swings else StructureConfiguration.NONE.value
                strength = StructureStrength.DEVELOPING.value
                reason = "Neutral state maintained; directional sequence developing"

        elif prior_state == MarketState.BULLISH.value:
            if ev_type == CanonicalStructureEventType.CHOCH_BEARISH:
                new_state = MarketState.TRANSITION.value
                config = StructureConfiguration.HH_HL.value  # Preserve previous structure context during transition
                strength = StructureStrength.DEVELOPING.value
                reason = "Bearish CHoCH detected; previous bullish structure moved into transition"

            elif ev_type == CanonicalStructureEventType.MSS_BEARISH:
                new_state = MarketState.TRANSITION.value
                config = StructureConfiguration.HH_HL.value
                strength = StructureStrength.DEVELOPING.value
                reason = "Bearish MSS detected; moved into transition (conservative regime shift)"

            elif ev_type == CanonicalStructureEventType.BOS_BEARISH:
                new_state = MarketState.TRANSITION.value
                config = StructureConfiguration.MIXED.value
                strength = StructureStrength.DEVELOPING.value
                reason = "Opposite BOS_BEARISH detected against BULLISH regime; moved to TRANSITION for confirmation"

            elif ev_type == CanonicalStructureEventType.DOUBLE_BREAK:
                new_state = MarketState.BULLISH.value
                config = StructureConfiguration.HH_HL.value
                strength = StructureStrength.CONFIRMED.value
                reason = "DOUBLE_BREAK event observed; preserves existing BULLISH state"

            else:
                # Continuation events (HH, HL, BOS_BULLISH)
                new_state = MarketState.BULLISH.value
                config = StructureConfiguration.HH_HL.value
                strength = StructureStrength.CONFIRMED.value
                reason = f"Bullish continuation reaffirmed by {ev_type.value}"

        elif prior_state == MarketState.BEARISH.value:
            if ev_type == CanonicalStructureEventType.CHOCH_BULLISH:
                new_state = MarketState.TRANSITION.value
                config = StructureConfiguration.LH_LL.value
                strength = StructureStrength.DEVELOPING.value
                reason = "Bullish CHoCH detected; previous bearish structure moved into transition"

            elif ev_type == CanonicalStructureEventType.MSS_BULLISH:
                new_state = MarketState.TRANSITION.value
                config = StructureConfiguration.LH_LL.value
                strength = StructureStrength.DEVELOPING.value
                reason = "Bullish MSS detected; moved into transition (conservative regime shift)"

            elif ev_type == CanonicalStructureEventType.BOS_BULLISH:
                new_state = MarketState.TRANSITION.value
                config = StructureConfiguration.MIXED.value
                strength = StructureStrength.DEVELOPING.value
                reason = "Opposite BOS_BULLISH detected against BEARISH regime; moved to TRANSITION for confirmation"

            elif ev_type == CanonicalStructureEventType.DOUBLE_BREAK:
                new_state = MarketState.BEARISH.value
                config = StructureConfiguration.LH_LL.value
                strength = StructureStrength.CONFIRMED.value
                reason = "DOUBLE_BREAK event observed; preserves existing BEARISH state"

            else:
                # Continuation events (LH, LL, BOS_BEARISH)
                new_state = MarketState.BEARISH.value
                config = StructureConfiguration.LH_LL.value
                strength = StructureStrength.CONFIRMED.value
                reason = f"Bearish continuation reaffirmed by {ev_type.value}"

        elif prior_state == MarketState.TRANSITION.value:
            # Check which regime we transitioned from
            prior_regime = st.get("previous_state", MarketState.BULLISH.value)

            if prior_regime == MarketState.BULLISH.value:
                # Awaiting bearish confirmation
                if ev_type == CanonicalStructureEventType.MSS_BEARISH or check_ordered_bearish_sequence(swings):
                    new_state = MarketState.BEARISH.value
                    config = StructureConfiguration.LH_LL.value
                    strength = StructureStrength.CONFIRMED.value
                    reason = "Bearish confirmation sequence established after transition"
                elif ev_type == CanonicalStructureEventType.BOS_BULLISH or check_ordered_bullish_sequence(swings):
                    # Transition invalidated; resumed original bullish trend
                    new_state = MarketState.BULLISH.value
                    config = StructureConfiguration.HH_HL.value
                    strength = StructureStrength.CONFIRMED.value
                    reason = "Bullish continuation resumed; transition invalidated"
                else:
                    new_state = MarketState.TRANSITION.value
                    config = StructureConfiguration.MIXED.value
                    strength = StructureStrength.DEVELOPING.value
                    reason = f"Transition state maintained; event {ev_type.value} observed"

            elif prior_regime == MarketState.BEARISH.value:
                # Awaiting bullish confirmation
                if ev_type == CanonicalStructureEventType.MSS_BULLISH or check_ordered_bullish_sequence(swings):
                    new_state = MarketState.BULLISH.value
                    config = StructureConfiguration.HH_HL.value
                    strength = StructureStrength.CONFIRMED.value
                    reason = "Bullish confirmation sequence established after transition"
                elif ev_type == CanonicalStructureEventType.BOS_BEARISH or check_ordered_bearish_sequence(swings):
                    # Transition invalidated; resumed original bearish trend
                    new_state = MarketState.BEARISH.value
                    config = StructureConfiguration.LH_LL.value
                    strength = StructureStrength.CONFIRMED.value
                    reason = "Bearish continuation resumed; transition invalidated"
                else:
                    new_state = MarketState.TRANSITION.value
                    config = StructureConfiguration.MIXED.value
                    strength = StructureStrength.DEVELOPING.value
                    reason = f"Transition state maintained; event {ev_type.value} observed"

            else:
                # Transition from NEUTRAL/UNKNOWN
                if check_ordered_bullish_sequence(swings):
                    new_state = MarketState.BULLISH.value
                    config = StructureConfiguration.HH_HL.value
                    strength = StructureStrength.CONFIRMED.value
                    reason = "Bullish confirmation sequence established"
                elif check_ordered_bearish_sequence(swings):
                    new_state = MarketState.BEARISH.value
                    config = StructureConfiguration.LH_LL.value
                    strength = StructureStrength.CONFIRMED.value
                    reason = "Bearish confirmation sequence established"
                else:
                    new_state = MarketState.NEUTRAL.value
                    config = StructureConfiguration.MIXED.value
                    strength = StructureStrength.DEVELOPING.value
                    reason = "Transition resolved to NEUTRAL due to mixed structural evidence"

        # Previous state semantics: state immediately before processing this event
        state_changed = (new_state != prior_state)
        st["previous_state"] = prior_state
        st["state"] = new_state
        st["structure"] = config
        st["structure_strength"] = strength
        st["state_changed"] = state_changed
        st["state_reason"] = reason

        logger.info(
            f"[STRUCTURE_STATE] symbol={st['symbol']} tf={st['timeframe']} "
            f"previous={prior_state} event={ev_type.value} new={new_state} "
            f"changed={state_changed} reason='{reason}'"
        )
        return st, True, reason


market_structure_state_engine = MarketStructureStateEngine()
