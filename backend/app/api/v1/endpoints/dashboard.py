import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Optional
from fastapi import APIRouter, Query
import psycopg

from backend.app.core.config import settings
from backend.app.services.event_logger import event_logger
from backend.app.services.supabase_service import get_postgres_connection

router = APIRouter(prefix="/dashboard", tags=["Dashboard Mission Control"])

_server_start_time = time.time()
_last_successful_db_time: Optional[datetime] = None

# Freshness thresholds in seconds per timeframe
TIMEFRAME_TOLERANCES = {
    "M1": {"sync": 90, "delayed": 180},
    "M5": {"sync": 360, "delayed": 720},
    "M15": {"sync": 1080, "delayed": 2160},
    "M30": {"sync": 2100, "delayed": 4200},
    "H1": {"sync": 4200, "delayed": 8400},
    "H4": {"sync": 16800, "delayed": 33600},
    "D1": {"sync": 100800, "delayed": 172800},
}

def format_age(seconds: int) -> str:
    if seconds < 0:
        return "just now"
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"

@router.get("/summary")
def get_dashboard_summary() -> dict:
    """
    Consolidated real-time operational state for the Alped Punya V3 Mission Control Room.
    Returns zero fake data — only authentic infrastructure state.
    """
    global _last_successful_db_time
    now_utc = datetime.now(timezone.utc)
    wib_tz = ZoneInfo("Asia/Jakarta")
    now_wib = now_utc.astimezone(wib_tz)

    # 1. Evaluate FastAPI Status
    uptime_sec = int(time.time() - _server_start_time)
    fastapi_status = {
        "status": "ONLINE",
        "uptime_seconds": uptime_sec,
        "uptime_display": format_age(uptime_sec),
        "version": settings.SCHEMA_VERSION,
        "host": settings.HOST,
        "port": settings.PORT
    }

    # 2. Evaluate Supabase Database Status & Latency
    supabase_status = {
        "status": "UNKNOWN",
        "latency_ms": None,
        "last_connected_at": _last_successful_db_time.isoformat() if _last_successful_db_time else None
    }

    db_conn = None
    t0 = time.perf_counter()
    try:
        db_conn = get_postgres_connection()
        if db_conn:
            with db_conn.cursor() as cur:
                cur.execute("SELECT 1;")
                cur.fetchone()
            latency = (time.perf_counter() - t0) * 1000
            supabase_status["status"] = "ONLINE"
            supabase_status["latency_ms"] = round(latency, 1)
            _last_successful_db_time = now_utc
            supabase_status["last_connected_at"] = now_utc.isoformat()
        else:
            supabase_status["status"] = "OFFLINE"
    except Exception as e:
        supabase_status["status"] = "OFFLINE"
        supabase_status["error"] = str(e)
        if db_conn:
            try:
                db_conn.close()
            except Exception:
                pass
            db_conn = None

    # 3. Evaluate MT5 EA Status
    mt5_status = {
        "status": "OFFLINE",
        "broker": "N/A",
        "environment": "N/A",
        "ea_identifier": "N/A",
        "ea_version": "N/A",
        "source_id": "N/A",
        "last_heartbeat_at": None,
        "age_seconds": None,
        "age_display": "N/A"
    }

    # 4. Initialize Market Data Matrix
    all_tfs = ["M1", "M5", "M15", "M30", "H1", "H4", "D1"]
    market_timeframes = {}
    for tf in all_tfs:
        market_timeframes[tf] = {
            "status": "NO_DATA",
            "timeframe": tf,
            "candle_time_utc": None,
            "candle_time_display": "N/A",
            "candle_time_wib": "N/A",
            "age_seconds": None,
            "age_display": "N/A",
            "open": None,
            "high": None,
            "low": None,
            "close": None,
            "tick_volume": None,
            "spread": None,
            "session": "N/A",
            "count": 0
        }

    # 5. Initialize Data Quality Metrics
    data_quality = {
        "total_candles": 0,
        "missing_candles": 0,
        "recovered_candles": 0,
        "rejected_candles": 0,
        "duplicate_candles": 0,
        "last_validation_time": None,
        "health_score_pct": 100.0
    }

    # 6. Initialize Account Telemetry
    account_data = {
        "balance": None,
        "equity": None,
        "free_margin": None,
        "margin": None,
        "margin_level": None,
        "positions_count": 0,
        "floating_pnl": None,
        "last_update_utc": None,
        "last_update_display": "N/A",
        "positions": []
    }

    last_market_data_received_utc = None

    # Query persistent data if DB connection succeeded
    if db_conn:
        try:
            with db_conn.cursor() as cur:
                # Query MT5 Source
                cur.execute("""
                    SELECT source_id, broker, environment, ea_identifier, ea_version, last_heartbeat_at
                    FROM sources
                    ORDER BY last_heartbeat_at DESC
                    LIMIT 1;
                """)
                src_row = cur.fetchone()
                if src_row:
                    s_id, broker, env, ea_id, ea_ver, hb_at = src_row
                    if hb_at:
                        age = int((now_utc - hb_at).total_seconds())
                        status_str = "ONLINE" if age <= 90 else ("DELAYED" if age <= 180 else "OFFLINE")
                        mt5_status.update({
                            "status": status_str,
                            "broker": broker,
                            "environment": env,
                            "ea_identifier": ea_id,
                            "ea_version": ea_ver,
                            "source_id": s_id,
                            "last_heartbeat_at": hb_at.isoformat(),
                            "age_seconds": age,
                            "age_display": format_age(age)
                        })

                # Query latest candle per timeframe for XAUUSD
                cur.execute("""
                    SELECT DISTINCT ON (timeframe)
                        timeframe, candle_time_utc, open, high, low, close, tick_volume, spread, session, created_at
                    FROM market_candles
                    WHERE symbol ILIKE '%XAUUSD%'
                    ORDER BY timeframe, candle_time_utc DESC;
                """)
                tf_rows = cur.fetchall()

                # Query counts per timeframe
                cur.execute("""
                    SELECT timeframe, count(*)
                    FROM market_candles
                    WHERE symbol ILIKE '%XAUUSD%'
                    GROUP BY timeframe;
                """)
                tf_counts = dict(cur.fetchall())

                for row in tf_rows:
                    tf, c_time, o, h, l, c, vol, spread, sess, cr_at = row
                    if not last_market_data_received_utc or (cr_at and cr_at > last_market_data_received_utc):
                        last_market_data_received_utc = cr_at

                    age = int((now_utc - c_time).total_seconds())
                    tolerances = TIMEFRAME_TOLERANCES.get(tf, {"sync": 300, "delayed": 600})
                    if age <= tolerances["sync"]:
                        tf_status = "SYNC"
                    elif age <= tolerances["delayed"]:
                        tf_status = "DELAYED"
                    else:
                        tf_status = "STALE"

                    c_wib = c_time.astimezone(wib_tz)
                    market_timeframes[tf] = {
                        "status": tf_status,
                        "timeframe": tf,
                        "candle_time_utc": c_time.isoformat(),
                        "candle_time_display": c_time.strftime("%H:%M:%S UTC"),
                        "candle_time_wib": c_wib.strftime("%H:%M:%S WIB"),
                        "age_seconds": age,
                        "age_display": format_age(age),
                        "open": float(o) if o is not None else None,
                        "high": float(h) if h is not None else None,
                        "low": float(l) if l is not None else None,
                        "close": float(c) if c is not None else None,
                        "tick_volume": vol,
                        "spread": spread,
                        "session": sess,
                        "count": tf_counts.get(tf, 0)
                    }

                # Query Data Quality Counts
                cur.execute("SELECT count(*) FROM market_candles;")
                data_quality["total_candles"] = cur.fetchone()[0]

                cur.execute("SELECT count(*) FROM market_candles WHERE is_gap_recovered = true;")
                data_quality["recovered_candles"] = cur.fetchone()[0]

                cur.execute("SELECT COALESCE(SUM(missing_bars_count), 0) FROM candle_gaps WHERE status NOT IN ('WEEKEND', 'MARKET_CLOSED', 'HOLIDAY');")
                data_quality["missing_candles"] = int(cur.fetchone()[0])

                if last_market_data_received_utc:
                    data_quality["last_validation_time"] = last_market_data_received_utc.isoformat()
                
                # Integrity score calculation
                if data_quality["total_candles"] > 0:
                    tot = data_quality["total_candles"]
                    miss = data_quality["missing_candles"]
                    data_quality["health_score_pct"] = round(max(0.0, 100.0 - (miss / tot * 100.0)), 2)

                # Query Account Snapshot
                cur.execute("""
                    SELECT balance, equity, margin, free_margin, margin_level, open_positions_count, snapshot_time_utc
                    FROM account_snapshots
                    ORDER BY snapshot_time_utc DESC
                    LIMIT 1;
                """)
                acc_row = cur.fetchone()
                if acc_row:
                    bal, eq, mar, fmar, m_lvl, p_count, s_time = acc_row
                    fl_pnl = float(eq - bal)
                    age_acc = int((now_utc - s_time).total_seconds())
                    account_data.update({
                        "balance": float(bal),
                        "equity": float(eq),
                        "margin": float(mar),
                        "free_margin": float(fmar),
                        "margin_level": float(m_lvl),
                        "positions_count": p_count,
                        "floating_pnl": round(fl_pnl, 2),
                        "last_update_utc": s_time.isoformat(),
                        "last_update_display": format_age(age_acc) + " ago"
                    })

                # Query Open Positions
                cur.execute("""
                    SELECT ticket, symbol, type, lots, open_price, current_price, sl, tp, profit, open_time_utc
                    FROM positions
                    ORDER BY open_time_utc DESC
                    LIMIT 10;
                """)
                for pos in cur.fetchall():
                    account_data["positions"].append({
                        "ticket": pos[0],
                        "symbol": pos[1],
                        "type": pos[2],
                        "lots": float(pos[3]),
                        "open_price": float(pos[4]),
                        "current_price": float(pos[5]),
                        "sl": float(pos[6]),
                        "tp": float(pos[7]),
                        "profit": float(pos[8]),
                        "open_time_utc": pos[9].isoformat() if pos[9] else None
                    })
        except Exception as e:
            supabase_status["query_error"] = str(e)
        finally:
            try:
                db_conn.close()
            except Exception:
                pass

    # 7. Overall Pipeline Status
    if mt5_status["status"] == "ONLINE" and supabase_status["status"] == "ONLINE":
        m1_status = market_timeframes["M1"]["status"]
        if m1_status in ("SYNC", "DELAYED") or data_quality["total_candles"] == 0:
            pipeline_status = {"status": "HEALTHY", "message": "All pipeline subsystems operational and healthy."}
        else:
            pipeline_status = {"status": "WARNING", "message": "Infrastructure online but market data flow is STALE."}
    elif mt5_status["status"] == "DELAYED" and supabase_status["status"] == "ONLINE":
        pipeline_status = {"status": "WARNING", "message": "MT5 heartbeat/data stream delayed."}
    elif supabase_status["status"] == "OFFLINE" or mt5_status["status"] == "OFFLINE":
        pipeline_status = {"status": "ERROR", "message": "Critical connection failure in pipeline components."}
    else:
        pipeline_status = {"status": "WARNING", "message": "Pipeline initializing."}

    # 8. Symbols List
    symbols_summary = [
        {
            "symbol": "XAUUSD.vx",
            "display_name": "XAUUSD (Spot Gold)",
            "latest_price": market_timeframes["M1"]["close"],
            "spread": market_timeframes["M1"]["spread"],
            "session": market_timeframes["M1"]["session"],
            "timeframes": market_timeframes
        }
    ]

    return {
        "status": "SUCCESS",
        "server_time_utc": now_utc.isoformat(),
        "server_time_display_utc": now_utc.strftime("%H:%M:%S UTC"),
        "server_time_display_wib": now_wib.strftime("%H:%M:%S WIB"),
        "connections": {
            "mt5": mt5_status,
            "fastapi": fastapi_status,
            "supabase": supabase_status,
            "pipeline": pipeline_status
        },
        "market": {
            "primary_symbol": "XAUUSD.vx",
            "symbols": symbols_summary
        },
        "data_quality": data_quality,
        "account": account_data,
        "recent_events": event_logger.get_events(limit=15),
        "last_dashboard_update": now_utc.isoformat(),
        "last_market_data_received": last_market_data_received_utc.isoformat() if last_market_data_received_utc else None
    }

@router.get("/candles")
def get_dashboard_candles(
    symbol: str = Query("XAUUSD.vx"),
    timeframe: str = Query("M1"),
    limit: int = Query(50, ge=1, le=200)
) -> dict:
    """
    Returns latest individual candles for detailed tabular inspection in the Market Data view.
    """
    db_conn = get_postgres_connection()
    if not db_conn:
        from backend.app.repositories.candle_repo import _memory_candles
        mem_candles = []
        sym_clean = symbol.split(".")[0].upper()
        for (_, sym, tf, _), c in _memory_candles.items():
            if sym_clean in sym.upper() and tf.upper() == timeframe.upper():
                c_epoch = c.get("candle_time_epoch", 0)
                mem_candles.append({
                    "id": c.get("id", 0),
                    "timeframe": tf,
                    "epoch": c_epoch,
                    "candle_time_utc": c.get("candle_time_utc"),
                    "candle_time_display": str(c.get("candle_time_utc", "")),
                    "candle_time_wib": str(c.get("broker_time", "")),
                    "open": float(c["open"]),
                    "high": float(c["high"]),
                    "low": float(c["low"]),
                    "close": float(c["close"]),
                    "volume": c.get("tick_volume", 0),
                    "spread": c.get("spread", 0),
                    "session": c.get("session", "N/A"),
                    "is_gap_recovered": c.get("is_gap_recovered", False)
                })
        mem_candles.sort(key=lambda x: x["epoch"], reverse=True)
        return {
            "status": "SUCCESS",
            "symbol": symbol,
            "timeframe": timeframe,
            "total_returned": len(mem_candles[:limit]),
            "candles": mem_candles[:limit]
        }

    candles = []
    try:
        with db_conn.cursor() as cur:
            cur.execute("""
                SELECT id, timeframe, candle_time_utc, open, high, low, close, tick_volume, spread, session, is_gap_recovered, created_at
                FROM market_candles
                WHERE symbol ILIKE %s AND timeframe = %s
                ORDER BY candle_time_utc DESC
                LIMIT %s;
            """, (f"%{symbol.split('.')[0]}%", timeframe.upper(), limit))
            for r in cur.fetchall():
                c_time = r[2]
                wib_tz = ZoneInfo("Asia/Jakarta")
                epoch_sec = int(c_time.timestamp())
                candles.append({
                    "id": r[0],
                    "timeframe": r[1],
                    "epoch": epoch_sec,
                    "candle_time_utc": c_time.isoformat(),
                    "candle_time_display": c_time.strftime("%Y-%m-%d %H:%M:%S UTC"),
                    "candle_time_wib": c_time.astimezone(wib_tz).strftime("%Y-%m-%d %H:%M:%S WIB"),
                    "open": float(r[3]),
                    "high": float(r[4]),
                    "low": float(r[5]),
                    "close": float(r[6]),
                    "volume": r[7],
                    "spread": r[8],
                    "session": r[9],
                    "is_gap_recovered": r[10]
                })
    except Exception as e:
        return {"status": "ERROR", "message": str(e), "candles": []}
    finally:
        db_conn.close()

    return {
        "status": "SUCCESS",
        "symbol": symbol,
        "timeframe": timeframe,
        "total_returned": len(candles),
        "candles": candles
    }

@router.get("/logs")
def get_dashboard_logs(
    limit: int = Query(100, ge=1, le=500),
    level: str = Query("ALL"),
    service: str = Query("ALL")
) -> dict:
    """
    Returns event stream with filtering capabilities for the Logs page.
    """
    events = event_logger.get_events(limit=limit, level=level, service=service)
    return {
        "status": "SUCCESS",
        "total": len(events),
        "logs": events
    }
