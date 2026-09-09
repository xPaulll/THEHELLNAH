from typing import Any

class TradingJournalService:
    """
    Interface for reconstructing position lifecycle events into journal entries.
    V1: Pure contract.
    """
    def generate_journal_entry(self, position_events: list[dict]) -> dict[str, Any]:
        return {"status": "V1_INTERFACE_ONLY"}
