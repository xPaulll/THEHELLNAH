import logging
from typing import Optional
from backend.app.services.supabase_service import get_supabase_client

logger = logging.getLogger(__name__)

_memory_symbols: dict[tuple[str, str], dict] = {}

class SymbolRepository:
    def upsert_symbol(self, data: dict) -> dict:
        client = get_supabase_client()
        key = (data["source_id"], data["symbol"])

        if client:
            try:
                res = client.table("symbols").upsert(
                    data, 
                    on_conflict="source_id,symbol"
                ).execute()
                if res.data:
                    return res.data[0]
            except Exception as e:
                logger.error(f"[SymbolRepo] Supabase upsert error: {type(e).__name__}: {e}")

        _memory_symbols[key] = data
        return data

    def get_symbol(self, source_id: str, symbol: str) -> Optional[dict]:
        client = get_supabase_client()
        if client:
            try:
                res = client.table("symbols").select("*")\
                    .eq("source_id", source_id)\
                    .eq("symbol", symbol)\
                    .execute()
                if res.data:
                    return res.data[0]
            except Exception:
                pass
        return _memory_symbols.get((source_id, symbol))

symbol_repo = SymbolRepository()
