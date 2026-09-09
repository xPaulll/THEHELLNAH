from abc import ABC, abstractmethod
from typing import Any

class BaseFeature(ABC):
    """
    Abstract interface for future quantitative market feature extractors.
    Strictly placeholder in V1 - no calculation is executed.
    """
    @abstractmethod
    def compute(self, data: Any) -> dict[str, Any]:
        pass
