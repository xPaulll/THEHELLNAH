from typing import Any
from backend.app.features.base_feature import BaseFeature

class MarketStructureFeature(BaseFeature):
    """
    Placeholder for future CHoCH, BOS, Swing High/Low detection.
    V1: Pure interface contract.
    """
    def compute(self, data: Any) -> dict[str, Any]:
        return {"status": "V1_INTERFACE_ONLY"}
