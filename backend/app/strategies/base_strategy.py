from abc import ABC, abstractmethod
from typing import Any, Optional

class BaseStrategy(ABC):
    """
    Abstract interface for future quantitative strategies.
    Strictly placeholder in V1 - no trading or execution logic allowed.
    """
    @abstractmethod
    def evaluate(self, market_context: Any) -> Optional[dict[str, Any]]:
        pass
