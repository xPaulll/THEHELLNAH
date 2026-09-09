from typing import Any

class BacktestMetricsService:
    """
    Interface for future statistical metrics computation:
    win rate, expectancy, average R, profit factor, max drawdown, setup frequency.
    V1: Pure contract.
    """
    def compute_metrics(self, trades: list[dict]) -> dict[str, Any]:
        return {"status": "V1_INTERFACE_ONLY"}
