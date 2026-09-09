from datetime import datetime, timezone
from typing import Optional
from collections import deque
import logging

logger = logging.getLogger("alped.events")

class EventLogger:
    def __init__(self, max_events: int = 500):
        self._events: deque = deque(maxlen=max_events)
        self.record_event(
            level="INFO",
            service="FastAPI",
            message="Alped Punya V3 Event Logger initialized."
        )

    def record_event(
        self,
        level: str,
        service: str,
        message: str,
        timestamp: Optional[datetime] = None
    ):
        ts = timestamp or datetime.now(timezone.utc)
        entry = {
            "id": len(self._events) + 1,
            "timestamp": ts.isoformat(),
            "timestamp_display": ts.strftime("%H:%M:%S"),
            "level": level.upper(),
            "service": service.upper(),
            "message": message
        }
        self._events.appendleft(entry)
        
        # Also mirror to Python standard logger
        log_msg = f"[{service.upper()}] {message}"
        if level.upper() == "ERROR":
            logger.error(log_msg)
        elif level.upper() == "WARNING":
            logger.warning(log_msg)
        else:
            logger.info(log_msg)

        # Broadcast live event to WebSocket clients
        try:
            from backend.app.services.websocket_manager import ws_manager
            ws_manager.emit_system_event(entry)
        except Exception:
            pass

    def get_events(
        self,
        limit: int = 100,
        level: Optional[str] = None,
        service: Optional[str] = None
    ) -> list[dict]:
        results = list(self._events)
        if level and level.upper() != "ALL":
            results = [e for e in results if e["level"] == level.upper()]
        if service and service.upper() != "ALL":
            results = [e for e in results if e["service"] == service.upper()]
        return results[:limit]

event_logger = EventLogger()
