import logging
from typing import Optional
from backend.app.core.constants import (
    SwingType,
    SwingClassification,
    DEFAULT_EQUAL_TOLERANCE_POINTS
)
from backend.app.repositories.symbol_repo import symbol_repo

logger = logging.getLogger(__name__)

def get_symbol_point(symbol: str, source_id: Optional[str] = None) -> float:
    """
    Resolves the point size for a symbol using a strict resolution hierarchy:
    1. Explicit instrument point/digits metadata from symbol_repo (case-insensitive)
    2. Configured instrument conventions (e.g. XAU/GOLD, JPY, or standard Forex)
    3. Global fallback (0.00001)
    """
    try:
        sym_meta = symbol_repo.get_symbol(source_id, symbol)
        if sym_meta:
            if "point" in sym_meta and sym_meta["point"] is not None and float(sym_meta["point"]) > 0:
                return float(sym_meta["point"])
            if "digits" in sym_meta and sym_meta["digits"] is not None and int(sym_meta["digits"]) >= 0:
                return 10.0 ** (-int(sym_meta["digits"]))
    except Exception:
        pass

    sym_upper = symbol.upper()
    if "XAU" in sym_upper or "GOLD" in sym_upper:
        return 0.01
    if "JPY" in sym_upper:
        return 0.01
    return 0.00001

def get_symbol_tolerance_points(symbol: str, custom_tolerance: Optional[float] = None) -> float:
    """
    Returns tolerance in points for Equal High / Equal Low determination.
    """
    if custom_tolerance is not None:
        return float(custom_tolerance)

    sym_upper = symbol.upper()
    for prefix, tol in DEFAULT_EQUAL_TOLERANCE_POINTS.items():
        if prefix != "DEFAULT" and prefix in sym_upper:
            return float(tol)

    return float(DEFAULT_EQUAL_TOLERANCE_POINTS.get("DEFAULT", 10.0))

class SwingDetector:
    """
    Step 1 Feature Engine: Deterministic N-Bar Fractal Swing Detector.
    Strict responsibilities:
    1. N-bar fractal detection on closed candles only (NO Bar 0).
    2. Swing High / Swing Low peak detection.
    3. HH / LH / EQH and HL / LL / EQL classification against previous same-side swing.
    4. Deterministic point-based tolerance for equal highs/lows.
    Zero trend prediction, zero BOS/CHoCH, zero strategy logic.
    """

    def __init__(self, fractal_n: int = 2):
        self.fractal_n = fractal_n

    def evaluate_candidate_at_index(
        self,
        candles: list[dict],
        candidate_idx: int,
        fractal_n: Optional[int] = None
    ) -> tuple[bool, bool]:
        """
        Evaluates whether candle at candidate_idx is a Swing High and/or Swing Low.
        Requires fractal_n bars on left AND fractal_n bars on right.
        Returns (is_high, is_low).
        """
        n = fractal_n if fractal_n is not None else self.fractal_n

        # Strict boundary check: must have at least n bars to left and n bars to right
        if candidate_idx - n < 0 or candidate_idx + n >= len(candles):
            return False, False

        cand_high = float(candles[candidate_idx]["high"])
        cand_low = float(candles[candidate_idx]["low"])

        is_high = True
        is_low = True

        for k in range(1, n + 1):
            left_high = float(candles[candidate_idx - k]["high"])
            left_low = float(candles[candidate_idx - k]["low"])
            right_high = float(candles[candidate_idx + k]["high"])
            right_low = float(candles[candidate_idx + k]["low"])

            # Peak condition: strictly greater than all n neighbors on left and right
            if not (cand_high > left_high and cand_high > right_high):
                is_high = False

            # Valley condition: strictly less than all n neighbors on left and right
            if not (cand_low < left_low and cand_low < right_low):
                is_low = False

            if not is_high and not is_low:
                break

        return is_high, is_low

    def classify_swing_high(
        self,
        current_price: float,
        previous_high: Optional[float],
        threshold_price: float
    ) -> Optional[str]:
        """
        Classifies Swing High against immediate previous confirmed Swing High.
        Rule:
        - delta <= threshold_price -> EQH
        - current > previous -> HH
        - current < previous -> LH
        - previous is None -> None (first high in sequence)
        """
        if previous_high is None:
            return None

        delta = abs(current_price - previous_high)
        if round(delta, 8) <= round(threshold_price, 8):
            return SwingClassification.EQH.value

        if current_price > previous_high:
            return SwingClassification.HH.value
        else:
            return SwingClassification.LH.value

    def classify_swing_low(
        self,
        current_price: float,
        previous_low: Optional[float],
        threshold_price: float
    ) -> Optional[str]:
        """
        Classifies Swing Low against immediate previous confirmed Swing Low.
        Rule:
        - delta <= threshold_price -> EQL
        - current < previous -> LL
        - current > previous -> HL
        - previous is None -> None (first low in sequence)
        """
        if previous_low is None:
            return None

        delta = abs(current_price - previous_low)
        if round(delta, 8) <= round(threshold_price, 8):
            return SwingClassification.EQL.value

        if current_price < previous_low:
            return SwingClassification.LL.value
        else:
            return SwingClassification.HL.value

    def detect_swings_from_candles(
        self,
        candles: list[dict],
        fractal_n: Optional[int] = None,
        symbol: str = "",
        source_id: str = "",
        prior_high: Optional[float] = None,
        prior_low: Optional[float] = None,
        custom_tolerance_points: Optional[float] = None,
        timeframe: str = ""
    ) -> list[dict]:
        """
        Processes closed candles ascending and returns all detected and classified swings.
        Can run over full historical range or over an incremental window with seeded prior_high/prior_low.
        """
        n = fractal_n if fractal_n is not None else self.fractal_n
        if len(candles) < (2 * n + 1):
            return []

        point = get_symbol_point(symbol, source_id)
        tol_points = get_symbol_tolerance_points(symbol, custom_tolerance_points)
        threshold_price = tol_points * point

        swings: list[dict] = []
        last_high = prior_high
        last_low = prior_low

        for i in range(n, len(candles) - n):
            is_high, is_low = self.evaluate_candidate_at_index(candles, i, n)
            cand = candles[i]
            confirm_candle = candles[i + n]

            # Swing High
            if is_high:
                price_high = float(cand["high"])
                classification_high = self.classify_swing_high(price_high, last_high, threshold_price)
                last_high = price_high

                swings.append({
                    "source_id": source_id,
                    "symbol": symbol,
                    "timeframe": timeframe or cand.get("timeframe", ""),
                    "swing_type": SwingType.HIGH.value,
                    "swing_candle_time_epoch": int(cand["candle_time_epoch"]),
                    "swing_price": round(price_high, 6),
                    "confirmed_at_candle_time_epoch": int(confirm_candle["candle_time_epoch"]),
                    "classification": classification_high,
                    "fractal_n": n
                })

            # Swing Low
            if is_low:
                price_low = float(cand["low"])
                classification_low = self.classify_swing_low(price_low, last_low, threshold_price)
                last_low = price_low

                swings.append({
                    "source_id": source_id,
                    "symbol": symbol,
                    "timeframe": timeframe or cand.get("timeframe", ""),
                    "swing_type": SwingType.LOW.value,
                    "swing_candle_time_epoch": int(cand["candle_time_epoch"]),
                    "swing_price": round(price_low, 6),
                    "confirmed_at_candle_time_epoch": int(confirm_candle["candle_time_epoch"]),
                    "classification": classification_low,
                    "fractal_n": n
                })

        return swings

swing_detector = SwingDetector()
