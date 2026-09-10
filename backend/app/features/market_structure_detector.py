import logging
from typing import Optional, Any
from backend.app.core.constants import (
    StructureBias,
    StructureTransitionState,
    StructureEventType,
    SwingType,
    SwingClassification,
    canonicalize_symbol,
    STRUCTURE_BREAK_TOLERANCE_POINTS
)
from backend.app.features.swing_detector import get_symbol_point

logger = logging.getLogger(__name__)

def get_break_tolerance_price(symbol: str, source_id: Optional[str] = None) -> float:
    """Calculates break threshold in price units: tolerance_points * symbol_point."""
    sym_canon = canonicalize_symbol(symbol)
    point = get_symbol_point(sym_canon, source_id)
    tol_pts = STRUCTURE_BREAK_TOLERANCE_POINTS.get("DEFAULT", 0.0)
    for prefix, pts in STRUCTURE_BREAK_TOLERANCE_POINTS.items():
        if prefix != "DEFAULT" and prefix in sym_canon:
            tol_pts = pts
            break
    return tol_pts * point

def create_initial_state(source_id: str, symbol: str, timeframe: str) -> dict:
    """Creates a fresh, deterministic initial structure state."""
    return {
        "source_id": source_id,
        "symbol": canonicalize_symbol(symbol),
        "timeframe": timeframe,
        "bias": StructureBias.NEUTRAL.value,
        "transition_state": StructureTransitionState.NORMAL.value,
        "last_processed_candle_time_epoch": 0,
        "last_processed_confirmation_time": 0,
        "protected_high_candle_time_epoch": None,
        "protected_high_price": None,
        "protected_low_candle_time_epoch": None,
        "protected_low_price": None,
        "bullish_break_level_candle_time_epoch": None,
        "bullish_break_level_price": None,
        "bearish_break_level_candle_time_epoch": None,
        "bearish_break_level_price": None,
        "last_broken_high_candle_time_epoch": None,
        "last_broken_low_candle_time_epoch": None,
        "pending_choch_event_id": None,
        "pending_choch_epoch": None,
        "pending_choch_continuation_target_price": None,
    }

def check_bearish_mss_sequence(post_choch_swings: list[dict]) -> Optional[tuple[dict, dict]]:
    """
    Checks if post_choch_swings contains a valid bearish structural sequence:
    LH -> LL or LL -> LH without intervening contrary directional swings (HH or HL).
    The sequence must terminate on a bearish swing (LH or LL), i.e. not superseded by contrary swings.
    Returns (latest_lh, latest_ll) if valid, else None.
    """
    dir_swings = [
        s for s in post_choch_swings
        if s.get("classification") in [
            SwingClassification.LH.value,
            SwingClassification.LL.value,
            SwingClassification.HH.value,
            SwingClassification.HL.value
        ]
    ]
    if len(dir_swings) < 2:
        return None

    # Latest directional swing must be bearish (LH or LL)
    last_swing = dir_swings[-1]
    cls_last = last_swing.get("classification")
    if cls_last not in [SwingClassification.LH.value, SwingClassification.LL.value]:
        return None

    target_cls = SwingClassification.LL.value if cls_last == SwingClassification.LH.value else SwingClassification.LH.value

    # Scan backwards from the swing immediately preceding the last swing
    for i in range(len(dir_swings) - 2, -1, -1):
        si = dir_swings[i]
        cls_i = si.get("classification")
        # If an intervening contrary swing (HH or HL) is encountered, the sequence path is broken
        if cls_i in [SwingClassification.HH.value, SwingClassification.HL.value]:
            return None
        if cls_i == target_cls:
            lh_swing = si if cls_i == SwingClassification.LH.value else last_swing
            ll_swing = last_swing if cls_last == SwingClassification.LL.value else si
            return (lh_swing, ll_swing)

    return None

def check_bullish_mss_sequence(post_choch_swings: list[dict]) -> Optional[tuple[dict, dict]]:
    """
    Checks if post_choch_swings contains a valid bullish structural sequence:
    HL -> HH or HH -> HL without intervening contrary directional swings (LH or LL).
    The sequence must terminate on a bullish swing (HL or HH), i.e. not superseded by contrary swings.
    Returns (latest_hl, latest_hh) if valid, else None.
    """
    dir_swings = [
        s for s in post_choch_swings
        if s.get("classification") in [
            SwingClassification.HL.value,
            SwingClassification.HH.value,
            SwingClassification.LH.value,
            SwingClassification.LL.value
        ]
    ]
    if len(dir_swings) < 2:
        return None

    # Latest directional swing must be bullish (HL or HH)
    last_swing = dir_swings[-1]
    cls_last = last_swing.get("classification")
    if cls_last not in [SwingClassification.HL.value, SwingClassification.HH.value]:
        return None

    target_cls = SwingClassification.HH.value if cls_last == SwingClassification.HL.value else SwingClassification.HL.value

    # Scan backwards from the swing immediately preceding the last swing
    for i in range(len(dir_swings) - 2, -1, -1):
        si = dir_swings[i]
        cls_i = si.get("classification")
        # If an intervening contrary swing (LH or LL) is encountered, the sequence path is broken
        if cls_i in [SwingClassification.LH.value, SwingClassification.LL.value]:
            return None
        if cls_i == target_cls:
            hl_swing = si if cls_i == SwingClassification.HL.value else last_swing
            hh_swing = last_swing if cls_last == SwingClassification.HH.value else si
            return (hl_swing, hh_swing)

    return None

class MarketStructureDetector:
    """
    Pure, deterministic market structure detector (Step 2).
    Processes chronological closed candles and confirmed market_swings.
    Does zero swing detection; strictly consumes Step 1 market_swings.
    """

    def apply_confirmed_swings_to_state(
        self,
        state: dict,
        new_swings: list[dict],
        all_confirmed_swings_history: Optional[list[dict]] = None
    ) -> tuple[dict, Optional[dict]]:
        """
        Step A: Applies newly available confirmed swings to state.
        Evaluates MSS confirmation via post-CHOCH sequence validation.
        NOTE: Pending CHOCH is NEVER cancelled by swing confirmation alone.
        It can only be invalidated by closed candle break of the continuation target.
        Returns (updated_state, mss_event_if_triggered).
        """
        if not new_swings and not all_confirmed_swings_history:
            return state, None

        st = dict(state)
        mss_event: Optional[dict] = None

        # Build full confirmed swings context
        combined_history = list(all_confirmed_swings_history or [])
        history_keys = {(s.get("swing_candle_time_epoch"), s.get("swing_type")) for s in combined_history}
        for sw in new_swings:
            k = (sw.get("swing_candle_time_epoch"), sw.get("swing_type"))
            if k not in history_keys:
                combined_history.append(sw)
                history_keys.add(k)

        # Sort combined history deterministically
        combined_history.sort(
            key=lambda s: (
                int(s.get("confirmed_at_candle_time_epoch", 0)),
                int(s.get("swing_candle_time_epoch", 0)),
                str(s.get("swing_type", "")),
                int(s.get("id", 0))
            )
        )

        # Sort newly available swings deterministically
        sorted_new_swings = sorted(
            new_swings,
            key=lambda s: (
                int(s.get("confirmed_at_candle_time_epoch", 0)),
                int(s.get("swing_candle_time_epoch", 0)),
                str(s.get("swing_type", "")),
                int(s.get("id", 0))
            )
        )

        for sw in sorted_new_swings:
            stype = sw.get("swing_type")
            cls = sw.get("classification")
            p = float(sw.get("swing_price", 0.0))
            sw_epoch = int(sw.get("swing_candle_time_epoch", 0))
            conf_epoch = int(sw.get("confirmed_at_candle_time_epoch", 0))

            # Update last confirmation cursor
            if conf_epoch > st["last_processed_confirmation_time"]:
                st["last_processed_confirmation_time"] = conf_epoch

            # EQH and EQL cannot become directional protected/break levels
            if cls in [SwingClassification.EQH.value, SwingClassification.EQL.value]:
                continue

            # 1. Neutral State Bootstrap: Requires HH + HL (Bullish) or LH + LL (Bearish)
            if st["bias"] == StructureBias.NEUTRAL.value:
                swings_up_to_now = [
                    s for s in combined_history
                    if int(s.get("confirmed_at_candle_time_epoch", 0)) <= conf_epoch
                    and s.get("classification") not in [SwingClassification.EQH.value, SwingClassification.EQL.value]
                ]
                history_highs = [s for s in swings_up_to_now if s.get("swing_type") == SwingType.HIGH.value]
                history_lows = [s for s in swings_up_to_now if s.get("swing_type") == SwingType.LOW.value]

                hh_swings = [s for s in history_highs if s.get("classification") == SwingClassification.HH.value]
                hl_swings = [s for s in history_lows if s.get("classification") == SwingClassification.HL.value]
                lh_swings = [s for s in history_highs if s.get("classification") == SwingClassification.LH.value]
                ll_swings = [s for s in history_lows if s.get("classification") == SwingClassification.LL.value]

                has_bull_evidence = bool(hh_swings and hl_swings)
                has_bear_evidence = bool(lh_swings and ll_swings)

                if has_bull_evidence and not has_bear_evidence:
                    st["bias"] = StructureBias.BULLISH.value
                    latest_hh = hh_swings[-1]
                    latest_hl = hl_swings[-1]
                    st["bullish_break_level_price"] = float(latest_hh["swing_price"])
                    st["bullish_break_level_candle_time_epoch"] = int(latest_hh["swing_candle_time_epoch"])
                    st["protected_low_price"] = float(latest_hl["swing_price"])
                    st["protected_low_candle_time_epoch"] = int(latest_hl["swing_candle_time_epoch"])
                    st["protected_high_price"] = None
                    st["protected_high_candle_time_epoch"] = None
                    st["bearish_break_level_price"] = None
                    st["bearish_break_level_candle_time_epoch"] = None

                elif has_bear_evidence and not has_bull_evidence:
                    st["bias"] = StructureBias.BEARISH.value
                    latest_lh = lh_swings[-1]
                    latest_ll = ll_swings[-1]
                    st["protected_high_price"] = float(latest_lh["swing_price"])
                    st["protected_high_candle_time_epoch"] = int(latest_lh["swing_candle_time_epoch"])
                    st["bearish_break_level_price"] = float(latest_ll["swing_price"])
                    st["bearish_break_level_candle_time_epoch"] = int(latest_ll["swing_candle_time_epoch"])
                    st["protected_low_price"] = None
                    st["protected_low_candle_time_epoch"] = None
                    st["bullish_break_level_price"] = None
                    st["bullish_break_level_candle_time_epoch"] = None

                elif has_bull_evidence and has_bear_evidence:
                    # Resolve recency: whichever sequence completed most recently
                    latest_hh = hh_swings[-1]
                    latest_hl = hl_swings[-1]
                    latest_lh = lh_swings[-1]
                    latest_ll = ll_swings[-1]
                    max_bull_epoch = max(int(latest_hh["swing_candle_time_epoch"]), int(latest_hl["swing_candle_time_epoch"]))
                    max_bear_epoch = max(int(latest_lh["swing_candle_time_epoch"]), int(latest_ll["swing_candle_time_epoch"]))
                    if max_bull_epoch >= max_bear_epoch:
                        st["bias"] = StructureBias.BULLISH.value
                        st["bullish_break_level_price"] = float(latest_hh["swing_price"])
                        st["bullish_break_level_candle_time_epoch"] = int(latest_hh["swing_candle_time_epoch"])
                        st["protected_low_price"] = float(latest_hl["swing_price"])
                        st["protected_low_candle_time_epoch"] = int(latest_hl["swing_candle_time_epoch"])
                        st["protected_high_price"] = None
                        st["protected_high_candle_time_epoch"] = None
                        st["bearish_break_level_price"] = None
                        st["bearish_break_level_candle_time_epoch"] = None
                    else:
                        st["bias"] = StructureBias.BEARISH.value
                        st["protected_high_price"] = float(latest_lh["swing_price"])
                        st["protected_high_candle_time_epoch"] = int(latest_lh["swing_candle_time_epoch"])
                        st["bearish_break_level_price"] = float(latest_ll["swing_price"])
                        st["bearish_break_level_candle_time_epoch"] = int(latest_ll["swing_candle_time_epoch"])
                        st["protected_low_price"] = None
                        st["protected_low_candle_time_epoch"] = None
                        st["bullish_break_level_price"] = None
                        st["bullish_break_level_candle_time_epoch"] = None

            # 2. Bullish State Updates & MSS Tracking
            elif st["bias"] == StructureBias.BULLISH.value:
                if cls == SwingClassification.HL.value:
                    st["protected_low_price"] = p
                    st["protected_low_candle_time_epoch"] = sw_epoch
                elif cls == SwingClassification.HH.value:
                    st["bullish_break_level_price"] = p
                    st["bullish_break_level_candle_time_epoch"] = sw_epoch

                # If pending CHoCH Bearish is active, check MSS sequence (STRICT SEQUENCE CHECK)
                # NOTE: A newly confirmed HH does NOT cancel pending CHoCH!
                if st["transition_state"] == StructureTransitionState.CHOCH_BEARISH_PENDING.value:
                    pending_ep = st.get("pending_choch_epoch") or 0
                    if conf_epoch > pending_ep:
                        post_choch_swings = [
                            s for s in combined_history
                            if int(s.get("confirmed_at_candle_time_epoch", 0)) > pending_ep
                            and int(s.get("confirmed_at_candle_time_epoch", 0)) <= conf_epoch
                            and s.get("classification") not in [SwingClassification.EQH.value, SwingClassification.EQL.value]
                        ]

                        # Verify structural sequence: LH -> LL or LL -> LH without intervening contrary swings
                        mss_pair = check_bearish_mss_sequence(post_choch_swings)
                        if mss_pair is not None:
                            latest_lh, latest_ll = mss_pair
                            mss_event = {
                                "source_id": st["source_id"],
                                "symbol": st["symbol"],
                                "timeframe": st["timeframe"],
                                "event_type": StructureEventType.MSS_BEARISH.value,
                                "event_candle_time_epoch": conf_epoch,
                                "broken_swing_candle_time_epoch": int(latest_ll["swing_candle_time_epoch"]),
                                "broken_swing_type": latest_ll["swing_type"],
                                "broken_swing_price": float(latest_ll["swing_price"]),
                                "broken_high_swing_candle_time_epoch": int(latest_lh["swing_candle_time_epoch"]),
                                "broken_high_swing_price": float(latest_lh["swing_price"]),
                                "broken_low_swing_candle_time_epoch": int(latest_ll["swing_candle_time_epoch"]),
                                "broken_low_swing_price": float(latest_ll["swing_price"]),
                                "candle_close": float(latest_ll["swing_price"]),
                                "break_threshold": 0.0,
                                "previous_bias": StructureBias.BULLISH.value,
                                "resulting_bias": StructureBias.BEARISH.value,
                                "transition_state": StructureTransitionState.NORMAL.value,
                                "fractal_n": int(latest_ll.get("fractal_n", 2)),
                                "decision_context": {
                                    "trigger": "POST_CHOCH_LH_AND_LL_SEQUENCE_CONFIRMED",
                                    "lh_epoch": int(latest_lh["swing_candle_time_epoch"]),
                                    "ll_epoch": int(latest_ll["swing_candle_time_epoch"]),
                                    "pending_choch_epoch": pending_ep
                                }
                            }
                            # Flip bias to BEARISH
                            st["bias"] = StructureBias.BEARISH.value
                            st["transition_state"] = StructureTransitionState.NORMAL.value
                            st["protected_high_price"] = float(latest_lh["swing_price"])
                            st["protected_high_candle_time_epoch"] = int(latest_lh["swing_candle_time_epoch"])
                            st["bearish_break_level_price"] = float(latest_ll["swing_price"])
                            st["bearish_break_level_candle_time_epoch"] = int(latest_ll["swing_candle_time_epoch"])
                            st["protected_low_price"] = None
                            st["protected_low_candle_time_epoch"] = None
                            st["bullish_break_level_price"] = None
                            st["bullish_break_level_candle_time_epoch"] = None
                            st["last_broken_high_candle_time_epoch"] = None
                            st["last_broken_low_candle_time_epoch"] = None
                            st["pending_choch_epoch"] = None
                            st["pending_choch_event_id"] = None
                            st["pending_choch_continuation_target_price"] = None

            # 3. Bearish State Updates & MSS Tracking
            elif st["bias"] == StructureBias.BEARISH.value:
                if cls == SwingClassification.LH.value:
                    st["protected_high_price"] = p
                    st["protected_high_candle_time_epoch"] = sw_epoch
                elif cls == SwingClassification.LL.value:
                    st["bearish_break_level_price"] = p
                    st["bearish_break_level_candle_time_epoch"] = sw_epoch

                # If pending CHoCH Bullish is active, check MSS sequence (STRICT SEQUENCE CHECK)
                # NOTE: A newly confirmed LL does NOT cancel pending CHoCH!
                if st["transition_state"] == StructureTransitionState.CHOCH_BULLISH_PENDING.value:
                    pending_ep = st.get("pending_choch_epoch") or 0
                    if conf_epoch > pending_ep:
                        post_choch_swings = [
                            s for s in combined_history
                            if int(s.get("confirmed_at_candle_time_epoch", 0)) > pending_ep
                            and int(s.get("confirmed_at_candle_time_epoch", 0)) <= conf_epoch
                            and s.get("classification") not in [SwingClassification.EQH.value, SwingClassification.EQL.value]
                        ]

                        # Verify structural sequence: HL -> HH or HH -> HL without intervening contrary swings
                        mss_pair = check_bullish_mss_sequence(post_choch_swings)
                        if mss_pair is not None:
                            latest_hl, latest_hh = mss_pair
                            mss_event = {
                                "source_id": st["source_id"],
                                "symbol": st["symbol"],
                                "timeframe": st["timeframe"],
                                "event_type": StructureEventType.MSS_BULLISH.value,
                                "event_candle_time_epoch": conf_epoch,
                                "broken_swing_candle_time_epoch": int(latest_hh["swing_candle_time_epoch"]),
                                "broken_swing_type": latest_hh["swing_type"],
                                "broken_swing_price": float(latest_hh["swing_price"]),
                                "broken_high_swing_candle_time_epoch": int(latest_hh["swing_candle_time_epoch"]),
                                "broken_high_swing_price": float(latest_hh["swing_price"]),
                                "broken_low_swing_candle_time_epoch": int(latest_hl["swing_candle_time_epoch"]),
                                "broken_low_swing_price": float(latest_hl["swing_price"]),
                                "candle_close": float(latest_hh["swing_price"]),
                                "break_threshold": 0.0,
                                "previous_bias": StructureBias.BEARISH.value,
                                "resulting_bias": StructureBias.BULLISH.value,
                                "transition_state": StructureTransitionState.NORMAL.value,
                                "fractal_n": int(latest_hh.get("fractal_n", 2)),
                                "decision_context": {
                                    "trigger": "POST_CHOCH_HL_AND_HH_SEQUENCE_CONFIRMED",
                                    "hl_epoch": int(latest_hl["swing_candle_time_epoch"]),
                                    "hh_epoch": int(latest_hh["swing_candle_time_epoch"]),
                                    "pending_choch_epoch": pending_ep
                                }
                            }
                            # Flip bias to BULLISH
                            st["bias"] = StructureBias.BULLISH.value
                            st["transition_state"] = StructureTransitionState.NORMAL.value
                            st["protected_low_price"] = float(latest_hl["swing_price"])
                            st["protected_low_candle_time_epoch"] = int(latest_hl["swing_candle_time_epoch"])
                            st["bullish_break_level_price"] = float(latest_hh["swing_price"])
                            st["bullish_break_level_candle_time_epoch"] = int(latest_hh["swing_candle_time_epoch"])
                            st["protected_high_price"] = None
                            st["protected_high_candle_time_epoch"] = None
                            st["bearish_break_level_price"] = None
                            st["bearish_break_level_candle_time_epoch"] = None
                            st["last_broken_high_candle_time_epoch"] = None
                            st["last_broken_low_candle_time_epoch"] = None
                            st["pending_choch_epoch"] = None
                            st["pending_choch_event_id"] = None
                            st["pending_choch_continuation_target_price"] = None

        return st, mss_event

    def evaluate_closed_candle(
        self,
        candle: dict,
        state: dict,
        threshold_price: float = 0.0,
        fractal_n: int = 2
    ) -> tuple[dict, Optional[dict]]:
        """
        Step B: Evaluates a closed candle against the current structure state.
        Uses candle close (not wick).
        Returns (updated_state, event_if_triggered).
        Strict event priority:
        1. DOUBLE_BREAK
        2. Pending-state invalidation (closed candle break past continuation target)
        3. (MSS confirmation evaluated in Step A)
        4. CHOCH
        5. BOS
        6. No event
        """
        st = dict(state)
        c_close = float(candle["close"])
        c_epoch = int(candle["candle_time_epoch"])
        st["last_processed_candle_time_epoch"] = c_epoch

        # Determine target levels to test
        target_high_price: Optional[float] = None
        target_high_epoch: Optional[int] = None
        target_low_price: Optional[float] = None
        target_low_epoch: Optional[int] = None

        if st["bias"] == StructureBias.BULLISH.value:
            target_high_price = st.get("bullish_break_level_price")
            target_high_epoch = st.get("bullish_break_level_candle_time_epoch")
            target_low_price = st.get("protected_low_price")
            target_low_epoch = st.get("protected_low_candle_time_epoch")
        elif st["bias"] == StructureBias.BEARISH.value:
            target_high_price = st.get("protected_high_price")
            target_high_epoch = st.get("protected_high_candle_time_epoch")
            target_low_price = st.get("bearish_break_level_price")
            target_low_epoch = st.get("bearish_break_level_candle_time_epoch")

        # Evaluate breaks (strict inequalities: > and <)
        break_high = False
        if target_high_price is not None:
            if round(c_close, 8) > round(target_high_price + threshold_price, 8):
                break_high = True

        break_low = False
        if target_low_price is not None:
            if round(c_close, 8) < round(target_low_price - threshold_price, 8):
                break_low = True

        # Priority 1: DOUBLE_BREAK
        if break_high and break_low:
            event = {
                "source_id": st["source_id"],
                "symbol": st["symbol"],
                "timeframe": st["timeframe"],
                "event_type": StructureEventType.DOUBLE_BREAK.value,
                "event_candle_time_epoch": c_epoch,
                "broken_swing_candle_time_epoch": target_high_epoch,
                "broken_swing_type": SwingType.HIGH.value,
                "broken_swing_price": target_high_price,
                "broken_high_swing_candle_time_epoch": target_high_epoch,
                "broken_high_swing_price": target_high_price,
                "broken_low_swing_candle_time_epoch": target_low_epoch,
                "broken_low_swing_price": target_low_price,
                "candle_close": c_close,
                "break_threshold": threshold_price,
                "previous_bias": st["bias"],
                "resulting_bias": st["bias"], # Preserved! No arbitrary jump
                "transition_state": st["transition_state"],
                "fractal_n": fractal_n,
                "decision_context": {
                    "high_level": target_high_price,
                    "low_level": target_low_price,
                    "close": c_close,
                    "note": "DOUBLE_BREAK_PRESERVES_BIAS"
                }
            }
            return st, event

        # Priority 2: Invalidation check via candle close for pending CHoCH
        # Bearish pending is invalidated ONLY if closed candle breaks original bullish continuation target + tolerance
        if st["transition_state"] == StructureTransitionState.CHOCH_BEARISH_PENDING.value:
            inval_target = st.get("pending_choch_continuation_target_price") or target_high_price
            if inval_target is not None and round(c_close, 8) > round(inval_target + threshold_price, 8):
                # Bullish continuation target reached! Cancel pending CHoCH
                st["transition_state"] = StructureTransitionState.NORMAL.value
                st["pending_choch_epoch"] = None
                st["pending_choch_event_id"] = None
                st["pending_choch_continuation_target_price"] = None
                if target_high_epoch != st.get("last_broken_high_candle_time_epoch"):
                    st["last_broken_high_candle_time_epoch"] = target_high_epoch
                    event = {
                        "source_id": st["source_id"],
                        "symbol": st["symbol"],
                        "timeframe": st["timeframe"],
                        "event_type": StructureEventType.BOS_BULLISH.value,
                        "event_candle_time_epoch": c_epoch,
                        "broken_swing_candle_time_epoch": target_high_epoch,
                        "broken_swing_type": SwingType.HIGH.value,
                        "broken_swing_price": inval_target,
                        "candle_close": c_close,
                        "break_threshold": threshold_price,
                        "previous_bias": StructureBias.BULLISH.value,
                        "resulting_bias": StructureBias.BULLISH.value,
                        "transition_state": StructureTransitionState.NORMAL.value,
                        "fractal_n": fractal_n,
                        "decision_context": {
                            "broken_level": inval_target,
                            "close": c_close,
                            "note": "CANCELLED_PENDING_CHOCH_AND_BOS"
                        }
                    }
                    return st, event
                return st, None

        # Bullish pending is invalidated ONLY if closed candle breaks original bearish continuation target - tolerance
        elif st["transition_state"] == StructureTransitionState.CHOCH_BULLISH_PENDING.value:
            inval_target = st.get("pending_choch_continuation_target_price") or target_low_price
            if inval_target is not None and round(c_close, 8) < round(inval_target - threshold_price, 8):
                # Bearish continuation target reached! Cancel pending CHoCH
                st["transition_state"] = StructureTransitionState.NORMAL.value
                st["pending_choch_epoch"] = None
                st["pending_choch_event_id"] = None
                st["pending_choch_continuation_target_price"] = None
                if target_low_epoch != st.get("last_broken_low_candle_time_epoch"):
                    st["last_broken_low_candle_time_epoch"] = target_low_epoch
                    event = {
                        "source_id": st["source_id"],
                        "symbol": st["symbol"],
                        "timeframe": st["timeframe"],
                        "event_type": StructureEventType.BOS_BEARISH.value,
                        "event_candle_time_epoch": c_epoch,
                        "broken_swing_candle_time_epoch": target_low_epoch,
                        "broken_swing_type": SwingType.LOW.value,
                        "broken_swing_price": inval_target,
                        "candle_close": c_close,
                        "break_threshold": threshold_price,
                        "previous_bias": StructureBias.BEARISH.value,
                        "resulting_bias": StructureBias.BEARISH.value,
                        "transition_state": StructureTransitionState.NORMAL.value,
                        "fractal_n": fractal_n,
                        "decision_context": {
                            "broken_level": inval_target,
                            "close": c_close,
                            "note": "CANCELLED_PENDING_CHOCH_AND_BOS"
                        }
                    }
                    return st, event
                return st, None

        # Priority 4: CHOCH
        if st["bias"] == StructureBias.BULLISH.value and st["transition_state"] == StructureTransitionState.NORMAL.value:
            if break_low and target_low_price is not None:
                st["transition_state"] = StructureTransitionState.CHOCH_BEARISH_PENDING.value
                st["pending_choch_epoch"] = c_epoch
                st["pending_choch_continuation_target_price"] = target_high_price
                event = {
                    "source_id": st["source_id"],
                    "symbol": st["symbol"],
                    "timeframe": st["timeframe"],
                    "event_type": StructureEventType.CHOCH_BEARISH.value,
                    "event_candle_time_epoch": c_epoch,
                    "broken_swing_candle_time_epoch": target_low_epoch,
                    "broken_swing_type": SwingType.LOW.value,
                    "broken_swing_price": target_low_price,
                    "candle_close": c_close,
                    "break_threshold": threshold_price,
                    "previous_bias": StructureBias.BULLISH.value,
                    "resulting_bias": StructureBias.BULLISH.value, # Bias preserved while pending!
                    "transition_state": StructureTransitionState.CHOCH_BEARISH_PENDING.value,
                    "fractal_n": fractal_n,
                    "decision_context": {"broken_protected_low": target_low_price, "close": c_close}
                }
                return st, event

        elif st["bias"] == StructureBias.BEARISH.value and st["transition_state"] == StructureTransitionState.NORMAL.value:
            if break_high and target_high_price is not None:
                st["transition_state"] = StructureTransitionState.CHOCH_BULLISH_PENDING.value
                st["pending_choch_epoch"] = c_epoch
                st["pending_choch_continuation_target_price"] = target_low_price
                event = {
                    "source_id": st["source_id"],
                    "symbol": st["symbol"],
                    "timeframe": st["timeframe"],
                    "event_type": StructureEventType.CHOCH_BULLISH.value,
                    "event_candle_time_epoch": c_epoch,
                    "broken_swing_candle_time_epoch": target_high_epoch,
                    "broken_swing_type": SwingType.HIGH.value,
                    "broken_swing_price": target_high_price,
                    "candle_close": c_close,
                    "break_threshold": threshold_price,
                    "previous_bias": StructureBias.BEARISH.value,
                    "resulting_bias": StructureBias.BEARISH.value, # Bias preserved while pending!
                    "transition_state": StructureTransitionState.CHOCH_BULLISH_PENDING.value,
                    "fractal_n": fractal_n,
                    "decision_context": {"broken_protected_high": target_high_price, "close": c_close}
                }
                return st, event

        # Priority 5: BOS
        if st["bias"] == StructureBias.BULLISH.value and st["transition_state"] == StructureTransitionState.NORMAL.value:
            if break_high and target_high_price is not None:
                if target_high_epoch != st.get("last_broken_high_candle_time_epoch"):
                    st["last_broken_high_candle_time_epoch"] = target_high_epoch
                    event = {
                        "source_id": st["source_id"],
                        "symbol": st["symbol"],
                        "timeframe": st["timeframe"],
                        "event_type": StructureEventType.BOS_BULLISH.value,
                        "event_candle_time_epoch": c_epoch,
                        "broken_swing_candle_time_epoch": target_high_epoch,
                        "broken_swing_type": SwingType.HIGH.value,
                        "broken_swing_price": target_high_price,
                        "candle_close": c_close,
                        "break_threshold": threshold_price,
                        "previous_bias": StructureBias.BULLISH.value,
                        "resulting_bias": StructureBias.BULLISH.value,
                        "transition_state": StructureTransitionState.NORMAL.value,
                        "fractal_n": fractal_n,
                        "decision_context": {"broken_level": target_high_price, "close": c_close}
                    }
                    return st, event

        elif st["bias"] == StructureBias.BEARISH.value and st["transition_state"] == StructureTransitionState.NORMAL.value:
            if break_low and target_low_price is not None:
                if target_low_epoch != st.get("last_broken_low_candle_time_epoch"):
                    st["last_broken_low_candle_time_epoch"] = target_low_epoch
                    event = {
                        "source_id": st["source_id"],
                        "symbol": st["symbol"],
                        "timeframe": st["timeframe"],
                        "event_type": StructureEventType.BOS_BEARISH.value,
                        "event_candle_time_epoch": c_epoch,
                        "broken_swing_candle_time_epoch": target_low_epoch,
                        "broken_swing_type": SwingType.LOW.value,
                        "broken_swing_price": target_low_price,
                        "candle_close": c_close,
                        "break_threshold": threshold_price,
                        "previous_bias": StructureBias.BEARISH.value,
                        "resulting_bias": StructureBias.BEARISH.value,
                        "transition_state": StructureTransitionState.NORMAL.value,
                        "fractal_n": fractal_n,
                        "decision_context": {"broken_level": target_low_price, "close": c_close}
                    }
                    return st, event

        # Priority 6: No event
        return st, None

market_structure_detector = MarketStructureDetector()
