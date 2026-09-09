from typing import Any, Optional
from backend.app.strategies.base_strategy import BaseStrategy

class StrategyAIntraday(BaseStrategy):
    """
    Interface contract for future Strategy A (Regular Intraday: 4H->1H->15M->5M).
    V1: Pure placeholder.
    """
    def evaluate(self, market_context: Any) -> Optional[dict[str, Any]]:
        return None
