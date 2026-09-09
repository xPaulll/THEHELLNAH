from typing import Any
from backend.app.features.base_feature import BaseFeature

class DisplacementFeature(BaseFeature):
    """
    Placeholder for future momentum, body ratio, and range expansion evaluation.
    V1: Pure interface contract.
    """
    def compute(self, data: Any) -> dict[str, Any]:
        return {"status": "V1_INTERFACE_ONLY"}
