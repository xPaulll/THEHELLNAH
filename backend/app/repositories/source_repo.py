from datetime import datetime, timezone
from typing import Optional
from backend.app.services.supabase_service import get_supabase_client

# In-memory store for fallback/testing
_memory_sources: dict[str, dict] = {}

class SourceRepository:
    def upsert_source(self, data: dict) -> dict:
        client = get_supabase_client()
        source_id = data["source_id"]
        now_str = datetime.now(timezone.utc).isoformat()
        data["last_heartbeat_at"] = now_str

        if client:
            try:
                res = client.table("sources").upsert(data).execute()
                if res.data:
                    return res.data[0]
            except Exception as e:
                pass

        # Fallback in-memory
        _memory_sources[source_id] = data
        return data

    def get_source(self, source_id: str) -> Optional[dict]:
        client = get_supabase_client()
        if client:
            try:
                res = client.table("sources").select("*").eq("source_id", source_id).execute()
                if res.data and len(res.data) > 0:
                    return res.data[0]
            except Exception:
                pass

        if source_id in _memory_sources:
            return _memory_sources[source_id]

        # Auto-heal / auto-register valid 64-char SHA256 source identity
        if isinstance(source_id, str) and len(source_id) == 64:
            now_str = datetime.now(timezone.utc).isoformat()
            auto_record = {
                "source_id": source_id,
                "broker": "UNKNOWN_BROKER",
                "environment": "DEMO",
                "account_hash": "auto_healed",
                "ea_identifier": "ALPED_BRIDGE",
                "ea_version": "3.0.0",
                "payload_version": "1.0.0",
                "schema_version": "1.0.0",
                "is_active": True,
                "last_heartbeat_at": now_str
            }
            return self.upsert_source(auto_record)

        return None

    def update_heartbeat(self, source_id: str) -> None:
        client = get_supabase_client()
        now_str = datetime.now(timezone.utc).isoformat()
        if client:
            try:
                client.table("sources").update({"last_heartbeat_at": now_str}).eq("source_id", source_id).execute()
            except Exception:
                pass
        if source_id in _memory_sources:
            _memory_sources[source_id]["last_heartbeat_at"] = now_str

source_repo = SourceRepository()
