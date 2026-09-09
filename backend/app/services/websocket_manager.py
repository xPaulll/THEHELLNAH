import asyncio
import json
import logging
import time
from typing import Set, Optional
from fastapi import WebSocket, WebSocketDisconnect
from datetime import datetime, timezone

logger = logging.getLogger("alped.websocket")

class WebSocketManager:
    def __init__(self, throttle_interval_sec: float = 0.25):
        self.active_connections: Set[WebSocket] = set()
        self.throttle_interval = throttle_interval_sec  # 250ms => ~4 updates/sec max
        self._last_broadcast_mono: float = 0.0
        self._pending_broadcast_task: Optional[asyncio.Task] = None
        self._pending_market_payload: Optional[dict] = None
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)
        logger.info(f"WebSocket client connected. Total active: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        self.active_connections.discard(websocket)
        logger.info(f"WebSocket client disconnected. Remaining: {len(self.active_connections)}")

    async def broadcast(self, message: dict):
        """
        Direct broadcast to all connected clients.
        Automatically prunes disconnected sockets.
        """
        if not self.active_connections:
            return

        json_str = json.dumps(message)
        dead_sockets = []
        for connection in list(self.active_connections):
            try:
                await connection.send_text(json_str)
            except Exception:
                dead_sockets.append(connection)

        for s in dead_sockets:
            self.active_connections.discard(s)

    async def queue_market_update(self, snapshot: dict):
        """
        Server-side throttling for LIVE_MARKET updates.
        Ensures max 4 updates/sec, but NEVER drops the latest state.
        """
        if not self.active_connections:
            return

        async with self._lock:
            self._pending_market_payload = snapshot
            now_mono = time.monotonic()
            elapsed = now_mono - self._last_broadcast_mono

            if elapsed >= self.throttle_interval:
                # Can dispatch immediately
                self._last_broadcast_mono = now_mono
                payload_to_send = self._pending_market_payload
                self._pending_market_payload = None
                
                envelope = {
                    "type": "LIVE_MARKET",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "data": payload_to_send
                }
                await self.broadcast(envelope)
            else:
                # Schedule delayed flush if not already scheduled
                if not self._pending_broadcast_task or self._pending_broadcast_task.done():
                    delay = self.throttle_interval - elapsed
                    self._pending_broadcast_task = asyncio.create_task(self._delayed_flush(delay))

    async def _delayed_flush(self, delay: float):
        await asyncio.sleep(delay)
        async with self._lock:
            if self._pending_market_payload and self.active_connections:
                now_mono = time.monotonic()
                self._last_broadcast_mono = now_mono
                payload_to_send = self._pending_market_payload
                self._pending_market_payload = None

                envelope = {
                    "type": "LIVE_MARKET",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "data": payload_to_send
                }
                await self.broadcast(envelope)

    async def broadcast_candle_closed(self, candle_data: dict):
        """
        High-priority immediate broadcast when Bar 0 closes into Bar 1.
        """
        envelope = {
            "type": "CANDLE_CLOSED",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": candle_data
        }
        await self.broadcast(envelope)

    async def broadcast_system_event(self, event_data: dict):
        """
        Immediate broadcast for live system event stream on System page.
        """
        envelope = {
            "type": "SYSTEM_EVENT",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": event_data
        }
        await self.broadcast(envelope)

    def emit_candle_closed(self, candle_data: dict):
        """
        Thread-safe caller from sync endpoints to broadcast CANDLE_CLOSED.
        """
        if not self.active_connections:
            return
        coro = self.broadcast_candle_closed(candle_data)
        if self._loop and self._loop.is_running():
            try:
                loop = asyncio.get_running_loop()
                if loop == self._loop:
                    loop.create_task(coro)
                    return
            except RuntimeError:
                pass
            asyncio.run_coroutine_threadsafe(coro, self._loop)

    def emit_system_event(self, event_data: dict):
        """
        Thread-safe caller from sync loggers to broadcast SYSTEM_EVENT.
        """
        if not self.active_connections:
            return
        coro = self.broadcast_system_event(event_data)
        if self._loop and self._loop.is_running():
            try:
                loop = asyncio.get_running_loop()
                if loop == self._loop:
                    loop.create_task(coro)
                    return
            except RuntimeError:
                pass
            asyncio.run_coroutine_threadsafe(coro, self._loop)

ws_manager = WebSocketManager()
