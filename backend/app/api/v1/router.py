from fastapi import APIRouter
from backend.app.api.v1.endpoints.auth import router as auth_router
from backend.app.api.v1.endpoints.symbols import router as symbols_router
from backend.app.api.v1.endpoints.candles import router as candles_router
from backend.app.api.v1.endpoints.ticks import router as ticks_router
from backend.app.api.v1.endpoints.account import router as account_router
from backend.app.api.v1.endpoints.health import router as health_router
from backend.app.api.v1.endpoints.dashboard import router as dashboard_router
from backend.app.api.v1.endpoints.swings import router as swings_router
from backend.app.api.v1.endpoints.market_structure import router as market_structure_router
from backend.app.api.v1.endpoints.market_structure_state_router import router as market_structure_state_router

api_v1_router = APIRouter(prefix="/api/v1")

api_v1_router.include_router(auth_router)
api_v1_router.include_router(symbols_router)
api_v1_router.include_router(candles_router)
api_v1_router.include_router(ticks_router)
api_v1_router.include_router(account_router)
api_v1_router.include_router(health_router)
api_v1_router.include_router(dashboard_router)
api_v1_router.include_router(swings_router)
api_v1_router.include_router(market_structure_router)
api_v1_router.include_router(market_structure_state_router)
