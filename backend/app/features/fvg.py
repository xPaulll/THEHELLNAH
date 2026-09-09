from typing import Any
from backend.app.features.base_feature import BaseFeature

class FVGFeature(BaseFeature):
    """
    Placeholder for future 3-candle Fair Value Gap detection and mitigation tracking.
    V1: Pure interface contract.
    """
    def compute(self, data: Any) -> dict[str, Any]:
        return {"status": "V1_INTERFACE_ONLY"}
