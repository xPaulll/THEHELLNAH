from backend.app.services.supabase_service import get_supabase_client

_memory_snapshots: list[dict] = []
_memory_positions: dict[tuple[str, int], dict] = {}  # (source_id, ticket) -> position
_memory_events: list[dict] = []

class AccountRepository:
    def record_snapshot(self, snapshot: dict) -> dict:
        client = get_supabase_client()
        if client:
            try:
                res = client.table("account_snapshots").insert(snapshot).execute()
                if res.data:
                    return res.data[0]
            except Exception:
                pass

        _memory_snapshots.append(snapshot)
        return snapshot

    def upsert_positions(self, positions: list[dict]) -> int:
        if not positions:
            return 0

        for p in positions:
            key = (p["source_id"], p["ticket"])
            _memory_positions[key] = p

        client = get_supabase_client()
        if client:
            try:
                res = client.table("positions").upsert(
                    positions,
                    on_conflict="source_id,ticket"
                ).execute()
                if res.data:
                    return len(res.data)
            except Exception:
                pass

        return len(positions)

    def record_position_events(self, events: list[dict]) -> int:
        if not events:
            return 0

        client = get_supabase_client()
        if client:
            try:
                res = client.table("position_events").insert(events).execute()
                if res.data:
                    return len(res.data)
            except Exception:
                pass

        _memory_events.extend(events)
        return len(events)

account_repo = AccountRepository()
