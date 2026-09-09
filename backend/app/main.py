from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from backend.app.api.v1.router import api_v1_router
from backend.app.api.v1.endpoints.market import router as market_router
from backend.app.core.config import settings

import asyncio
from contextlib import asynccontextmanager
from backend.app.services.websocket_manager import ws_manager

@asynccontextmanager
async def lifespan(app: FastAPI):
    ws_manager._loop = asyncio.get_running_loop()
    yield

app = FastAPI(
    title="Alped Punya V3 — Trading Infrastructure API",
    version=settings.SCHEMA_VERSION,
    description="High-integrity data ingestion bridge connecting MT5 to Supabase with timezone-aware session tagging.",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount API & WebSocket routes
app.include_router(api_v1_router)
app.include_router(market_router)

# Mount Frontend Dashboard Static Files
FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/dashboard", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="dashboard")

@app.get("/")
def root():
    return RedirectResponse(url="/dashboard/")

@app.get("/api")
def api_status():
    return {
        "name": "Alped Punya V3 — V1 Data Pipeline",
        "version": settings.SCHEMA_VERSION,
        "status": "ONLINE",
        "docs_url": "/docs",
        "dashboard_url": "/dashboard/"
    }

