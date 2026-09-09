from typing import Any, Optional
from backend.app.strategies.base_strategy import BaseStrategy

class StrategyBSilverBullet(BaseStrategy):
    """
    Interface contract for future Strategy B (ICT Silver Bullet).
    V1: Pure placeholder.
    """
    def evaluate(self, market_context: Any) -> Optional[dict[str, Any]]:
        return None
