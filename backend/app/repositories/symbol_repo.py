import logging
from typing import Optional
from backend.app.services.supabase_service import get_supabase_client

logger = logging.getLogger(__name__)

_memory_symbols: dict[tuple[str, str], dict] = {}

class SymbolRepository:
    def upsert_symbol(self, data: dict) -> dict:
        client = get_supabase_client()
        source_id = data.get("source_id", "")
        symbol = data.get("symbol", "")
        key = (source_id, symbol.upper())

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

    def get_symbol(self, source_id: Optional[str], symbol: str) -> Optional[dict]:
        """
        Retrieves instrument metadata for symbol with case-insensitive matching.
        Resolution hierarchy:
        1. Exact source_id + case-insensitive symbol match
        2. Any source_id with case-insensitive symbol match
        3. In-memory lookup with case-insensitivity
        """
        if not symbol:
            return None

        client = get_supabase_client()
        if client:
            try:
                query = client.table("symbols").select("*")
                if source_id:
                    query = query.eq("source_id", source_id)
                res = query.ilike("symbol", symbol).limit(1).execute()
                if res.data and len(res.data) > 0:
                    return res.data[0]

                # Fallback without source_id if not found with source_id
                if source_id:
                    fallback_res = client.table("symbols").select("*").ilike("symbol", symbol).limit(1).execute()
                    if fallback_res.data and len(fallback_res.data) > 0:
                        return fallback_res.data[0]
            except Exception as e:
                logger.error(f"[SymbolRepo] Supabase get_symbol error: {type(e).__name__}: {e}")

        # In-memory lookup (case-insensitive)
        sym_upper = symbol.upper()
        if source_id:
            if (source_id, sym_upper) in _memory_symbols:
                return _memory_symbols[(source_id, sym_upper)]

        for (s_id, s_sym), data in _memory_symbols.items():
            if s_sym == sym_upper:
                return data

        return None

symbol_repo = SymbolRepository()
