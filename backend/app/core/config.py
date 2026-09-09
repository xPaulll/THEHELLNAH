from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

BASE_DIR = Path(__file__).resolve().parent.parent.parent

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(
            str(BASE_DIR / ".env"),
            str(BASE_DIR.parent / ".env"),
            ".env",
            "backend/.env",
        ),
        env_file_encoding="utf-8",
        extra="ignore"
    )

    ENVIRONMENT: str = "development"
    HOST: str = "127.0.0.1"
    PORT: int = 8000
    API_KEY: str = "alped_secret_key_v3_secure"

    # Database Settings (Direct PostgreSQL connection string)
    DATABASE_URL: str = ""  # e.g. postgresql://postgres:[PASSWORD]@db.zmzisvwilaeoydxcxjdd.supabase.co:5432/postgres

    # Supabase REST Settings
    SUPABASE_URL: str = ""  # e.g. https://zmzisvwilaeoydxcxjdd.supabase.co
    SUPABASE_KEY: str = ""

    # Version Tracking
    SCHEMA_VERSION: str = "1.0.0"
    DEFAULT_PAYLOAD_VERSION: str = "1.0.0"
    EXPECTED_EA_VERSION: str = "3.0.0"

    # Strategy Mode Configuration
    STRATEGY_MODE: str = "BOTH"  # INTRADAY, SILVER_BULLET, BOTH
    ALLOW_EXECUTION: bool = False  # Strictly False in V1

    # Ingestion Controls
    DEFAULT_TICK_MODE: str = "TICK_OFF"
    INITIAL_SYNC_BARS: int = 1000
    BATCH_SIZE: int = 200

settings = Settings()
