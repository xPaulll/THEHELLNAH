from backend.app.services.supabase_service import get_supabase_client

_memory_ticks: list[dict] = []

class TickRepository:
    def insert_ticks(self, ticks: list[dict]) -> int:
        if not ticks:
            return 0

        client = get_supabase_client()
        if client:
            try:
                res = client.table("market_ticks").insert(ticks).execute()
                if res.data:
                    return len(res.data)
            except Exception:
                pass

        _memory_ticks.extend(ticks)
        return len(ticks)

tick_repo = TickRepository()
