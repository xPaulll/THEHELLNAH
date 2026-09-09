from typing import Any
from backend.app.features.base_feature import BaseFeature

class LiquidityFeature(BaseFeature):
    """
    Placeholder for future Asia H/L, London H/L, PDH/PDL, EQH/EQL, and sweep detection.
    V1: Pure interface contract.
    """
    def compute(self, data: Any) -> dict[str, Any]:
        return {"status": "V1_INTERFACE_ONLY"}
