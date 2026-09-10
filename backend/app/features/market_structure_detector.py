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
    }

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
        Evaluates MSS confirmation or pending CHoCH invalidation via swings.
        Returns (updated_state, mss_event_if_triggered).
        """
        if not new_swings and not all_confirmed_swings_history:
            return state, None

        st = dict(state)
        mss_event: Optional[dict] = None

        # Build full confirmed swings context
        combined_history = list(all_confirmed_swings_history or [])
        # Ensure new_swings are in history
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
                # Filter confirmed history available up to this swing's confirmation time
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

                # If pending CHoCH Bearish is active, check MSS sequence
                if st["transition_state"] == StructureTransitionState.CHOCH_BEARISH_PENDING.value:
                    pending_ep = st.get("pending_choch_epoch") or 0
                    if conf_epoch > pending_ep:
                        # Invalidation check via confirmed swing: new confirmed HH cancels pending CHoCH
                        if cls == SwingClassification.HH.value:
                            st["transition_state"] = StructureTransitionState.NORMAL.value
                            st["pending_choch_epoch"] = None
                            st["pending_choch_event_id"] = None
                        else:
                            # Check post-CHoCH confirmed swings (confirmed_at > pending_ep)
                            # Must have >= 2 confirmed swings with at least 1 LH and at least 1 LL
                            post_choch_swings = [
                                s for s in combined_history
                                if int(s.get("confirmed_at_candle_time_epoch", 0)) > pending_ep
                                and int(s.get("confirmed_at_candle_time_epoch", 0)) <= conf_epoch
                                and s.get("classification") not in [SwingClassification.EQH.value, SwingClassification.EQL.value]
                            ]
                            has_post_lh = any(s.get("classification") == SwingClassification.LH.value for s in post_choch_swings)
                            has_post_ll = any(s.get("classification") == SwingClassification.LL.value for s in post_choch_swings)

                            if len(post_choch_swings) >= 2 and has_post_lh and has_post_ll:
                                # MSS Bearish confirmed!
                                latest_lh = next(s for s in reversed(post_choch_swings) if s.get("classification") == SwingClassification.LH.value)
                                latest_ll = next(s for s in reversed(post_choch_swings) if s.get("classification") == SwingClassification.LL.value)

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
                                        "trigger": "POST_CHOCH_LH_AND_LL_CONFIRMED",
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

            # 3. Bearish State Updates & MSS Tracking
            elif st["bias"] == StructureBias.BEARISH.value:
                if cls == SwingClassification.LH.value:
                    st["protected_high_price"] = p
                    st["protected_high_candle_time_epoch"] = sw_epoch
                elif cls == SwingClassification.LL.value:
                    st["bearish_break_level_price"] = p
                    st["bearish_break_level_candle_time_epoch"] = sw_epoch

                # If pending CHoCH Bullish is active, check MSS sequence
                if st["transition_state"] == StructureTransitionState.CHOCH_BULLISH_PENDING.value:
                    pending_ep = st.get("pending_choch_epoch") or 0
                    if conf_epoch > pending_ep:
                        # Invalidation check via confirmed swing: new confirmed LL cancels pending CHoCH
                        if cls == SwingClassification.LL.value:
                            st["transition_state"] = StructureTransitionState.NORMAL.value
                            st["pending_choch_epoch"] = None
                            st["pending_choch_event_id"] = None
                        else:
                            # Check post-CHoCH confirmed swings (confirmed_at > pending_ep)
                            # Must have >= 2 confirmed swings with at least 1 HL and at least 1 HH
                            post_choch_swings = [
                                s for s in combined_history
                                if int(s.get("confirmed_at_candle_time_epoch", 0)) > pending_ep
                                and int(s.get("confirmed_at_candle_time_epoch", 0)) <= conf_epoch
                                and s.get("classification") not in [SwingClassification.EQH.value, SwingClassification.EQL.value]
                            ]
                            has_post_hl = any(s.get("classification") == SwingClassification.HL.value for s in post_choch_swings)
                            has_post_hh = any(s.get("classification") == SwingClassification.HH.value for s in post_choch_swings)

                            if len(post_choch_swings) >= 2 and has_post_hl and has_post_hh:
                                # MSS Bullish confirmed!
                                latest_hl = next(s for s in reversed(post_choch_swings) if s.get("classification") == SwingClassification.HL.value)
                                latest_hh = next(s for s in reversed(post_choch_swings) if s.get("classification") == SwingClassification.HH.value)

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
                                        "trigger": "POST_CHOCH_HL_AND_HH_CONFIRMED",
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
        2. Pending-state invalidation
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
        if st["transition_state"] == StructureTransitionState.CHOCH_BEARISH_PENDING.value:
            if break_high and target_high_price is not None:
                # Bullish continuation target reached! Cancel pending CHoCH
                st["transition_state"] = StructureTransitionState.NORMAL.value
                st["pending_choch_epoch"] = None
                st["pending_choch_event_id"] = None
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
                        "decision_context": {
                            "broken_level": target_high_price,
                            "close": c_close,
                            "note": "CANCELLED_PENDING_CHOCH_AND_BOS"
                        }
                    }
                    return st, event
                return st, None

        elif st["transition_state"] == StructureTransitionState.CHOCH_BULLISH_PENDING.value:
            if break_low and target_low_price is not None:
                # Bearish continuation target reached! Cancel pending CHoCH
                st["transition_state"] = StructureTransitionState.NORMAL.value
                st["pending_choch_epoch"] = None
                st["pending_choch_event_id"] = None
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
                        "decision_context": {
                            "broken_level": target_low_price,
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
