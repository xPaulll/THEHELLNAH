import hashlib
from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader
from backend.app.core.config import settings

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

def generate_source_id(broker: str, environment: str, account_hash: str, ea_identifier: str) -> str:
    """
    Deterministic authority for generating a 64-character SHA-256 source_id.
    Normalizes inputs:
    - broker: strip, uppercase
    - environment: strip, uppercase
    - account_hash: strip, lowercase
    - ea_identifier: strip, uppercase
    """
    clean_broker = broker.strip().upper()
    clean_env = environment.strip().upper()
    clean_hash = account_hash.strip().lower()
    clean_ea_id = ea_identifier.strip().upper()

    raw_identity = f"{clean_broker}:{clean_env}:{clean_hash}:{clean_ea_id}"
    computed_hash = hashlib.sha256(raw_identity.encode("utf-8")).hexdigest()

    if len(computed_hash) != 64:
        raise ValueError(f"Generated source_id must be exactly 64 characters, got {len(computed_hash)}")

    return computed_hash

def verify_api_key(api_key: str = Security(api_key_header)) -> str:
    """
    Validates the X-API-Key header against configured settings.
    """
    if not settings.API_KEY:
        return "development_unsecured"
    if api_key != settings.API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-API-Key authentication token"
        )
    return api_key
