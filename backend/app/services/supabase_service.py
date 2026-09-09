import logging
from typing import Optional
from supabase import create_client, Client
import psycopg
from backend.app.core.config import settings

logger = logging.getLogger(__name__)

_supabase_client: Optional[Client] = None

def get_supabase_client() -> Optional[Client]:
    """
    Returns initialized Supabase Client if credentials are configured, else None.
    """
    global _supabase_client
    if _supabase_client is not None:
        return _supabase_client

    if settings.SUPABASE_URL and settings.SUPABASE_KEY and "your-" not in settings.SUPABASE_KEY:
        try:
            _supabase_client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
            logger.info("Supabase REST client initialized successfully")
        except Exception as e:
            logger.error(f"Failed to connect to Supabase REST: {e}")
            _supabase_client = None

    return _supabase_client

def get_postgres_connection():
    """
    Returns direct PostgreSQL connection via psycopg if DATABASE_URL is configured.
    """
    if settings.DATABASE_URL and "[YOUR-PASSWORD]" not in settings.DATABASE_URL:
        try:
            return psycopg.connect(settings.DATABASE_URL)
        except Exception as e:
            logger.error(f"Failed to connect directly to PostgreSQL: {e}")
            return None
    return None
