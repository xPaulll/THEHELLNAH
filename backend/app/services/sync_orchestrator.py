import time
import logging
from typing import Optional
from backend.app.models.market_data import BatchCandlePayload, LiveCandlePayload, SyncStatusResponse, CandleItem
from backend.app.services.timezone_service import verify_timestamp_consistency
from backend.app.services.session_resolver import get_primary_session
from backend.app.services.data_quality_service import validate_candle_sanity, detect_timeline_gaps
from backend.app.services.rebuild_manager import rebuild_manager
from backend.app.repositories.candle_repo import candle_repo
from backend.app.repositories.symbol_repo import symbol_repo
from backend.app.core.constants import Timeframe, SyncType

logger = logging.getLogger(__name__)

class SyncOrchestrator:
    def process_candle_batch(self, payload: BatchCandlePayload, run_features: bool = True) -> dict:
        t0 = time.perf_counter()
        source_id = payload.source_id
        symbol = payload.symbol
        timeframe_str = payload.timeframe.value

        logger.info(
            f"[Diagnostic] Request received: {len(payload.candles)} {timeframe_str} candles "
            f"for {symbol} (sync_type={payload.sync_type.value}, source={source_id[:8]}...)"
        )

        # Fetch symbol metadata to verify precision if registered
        sym_meta = symbol_repo.get_symbol(source_id, symbol)
        expected_digits = sym_meta["digits"] if sym_meta else None

        # 1. Validation: Timestamp consistency & Bar sanity
        t_parse_start = time.perf_counter()
        prepared_rows: list[dict] = []
        for c in payload.candles:
            # Bar sanity check
            validate_candle_sanity(c, expected_digits=expected_digits)

            # 3-Way Timestamp check
            canonical_utc = verify_timestamp_consistency(
                candle_time_epoch=c.candle_time_epoch,
                client_utc_str=c.candle_time_utc,
                broker_time_str=c.broker_time,
                broker_gmt_offset=c.broker_gmt_offset
            )

            session_tag = get_primary_session(canonical_utc)

            prepared_rows.append({
                "source_id": source_id,
                "symbol": symbol,
                "timeframe": timeframe_str,
                "candle_time_utc": canonical_utc.isoformat(),
                "candle_time_epoch": c.candle_time_epoch,
                "open": float(c.open),
                "high": float(c.high),
                "low": float(c.low),
                "close": float(c.close),
                "tick_volume": c.tick_volume,
                "spread": c.spread,
                "broker_time": c.broker_time,
                "broker_gmt_offset": c.broker_gmt_offset,
                "session": session_tag,
                "is_gap_recovered": (payload.sync_type in (SyncType.RECOVERY_SYNC, SyncType.GAP_BACKFILL)),
                "payload_version": payload.payload_version,
                "schema_version": payload.schema_version
            })
        t_parse_end = time.perf_counter()

        # 2. Timeline Gap Scanning within batch
        gaps = detect_timeline_gaps(
            candles=payload.candles,
            timeframe=timeframe_str,
            source_id=source_id,
            symbol=symbol
        )
        if gaps:
            candle_repo.record_gaps(gaps)

        # 3. Record gap recovery event in candle_gaps if GAP_BACKFILL / RECOVERY_SYNC
        t_audit_start = time.perf_counter()
        if payload.sync_type in (SyncType.GAP_BACKFILL, SyncType.RECOVERY_SYNC) and prepared_rows:
            from datetime import datetime, timezone
            from backend.app.core.constants import TIMEFRAME_SECONDS, GapStatus
            now_iso = datetime.now(timezone.utc).isoformat()
            sorted_rows = sorted(prepared_rows, key=lambda x: x["candle_time_epoch"])
            backfill_audit = {
                "source_id": source_id,
                "symbol": symbol,
                "timeframe": timeframe_str,
                "gap_start_utc": sorted_rows[0]["candle_time_utc"],
                "gap_end_utc": sorted_rows[-1]["candle_time_utc"],
                "expected_interval_seconds": TIMEFRAME_SECONDS.get(timeframe_str, 60),
                "missing_bars_count": len(sorted_rows),
                "status": GapStatus.RECOVERED.value,
                "classification_reason": f"Backfilled {len(sorted_rows)} bars via {payload.sync_type.value}",
                "detected_at": now_iso,
                "recovered_at": now_iso
            }
            candle_repo.record_gaps([backfill_audit])
        t_audit_end = time.perf_counter()

        # 4. Idempotent Upsert into market_candles
        t_db_start = time.perf_counter()
        inserted_count = candle_repo.upsert_candles(prepared_rows)
        t_db_end = time.perf_counter()

        logger.info(
            f"[Diagnostic] DB write finished: {inserted_count} candles inserted for {symbol} ({timeframe_str}) "
            f"[parse: {(t_parse_end - t_parse_start)*1000:.1f}ms, audit: {(t_audit_end - t_audit_start)*1000:.1f}ms, "
            f"DB upsert: {(t_db_end - t_db_start)*1000:.1f}ms, total elapsed: {(time.perf_counter() - t0)*1000:.1f}ms]"
        )

        # 5. Feature Engine execution (synchronous fallback if requested)
        if run_features:
            self.run_feature_engine(source_id, symbol, timeframe_str, payload.sync_type)

        return {
            "status": "SUCCESS",
            "source_id": source_id,
            "symbol": symbol,
            "timeframe": timeframe_str,
            "processed_count": inserted_count,
            "detected_gaps_count": len(gaps),
            "sync_type": payload.sync_type.value
        }

    def run_feature_engine(
        self,
        source_id: str,
        symbol: str,
        timeframe_str: str,
        sync_type: SyncType
    ) -> None:
        """
        Executes Step 1 (Swings), Step 2 (Market Structure), and Step 3 (State Engine)
        with per-key serialization (Lock) and comprehensive exception logging.
        Guarantees that two rebuilds for the same (source_id, symbol, timeframe) never run concurrently.
        """
        from backend.app.services.market_data_service import market_data_service

        lock = rebuild_manager.get_lock(source_id, symbol, timeframe_str)
        with lock:
            rebuild_manager.mark_started(source_id, symbol, timeframe_str)
            t0 = time.perf_counter()
            try:
                logger.info(
                    f"[Diagnostic] Feature Engine started for {symbol} ({timeframe_str}) under {sync_type.value}..."
                )
                if sync_type in (SyncType.INITIAL_SYNC, SyncType.RECOVERY_SYNC, SyncType.GAP_BACKFILL):
                    t_sw0 = time.perf_counter()
                    sw_count = market_data_service.recalculate_swings_full(
                        source_id=source_id,
                        symbol=symbol,
                        timeframe=timeframe_str
                    )
                    t_sw1 = time.perf_counter()

                    t_ms0 = time.perf_counter()
                    ev_count = market_data_service.recalculate_market_structure_full(
                        source_id=source_id,
                        symbol=symbol,
                        timeframe=timeframe_str
                    )
                    t_ms1 = time.perf_counter()

                    logger.info(
                        f"[Diagnostic] Feature Engine finished for {symbol} ({timeframe_str}): "
                        f"Step 1 swings={sw_count} ({(t_sw1 - t_sw0)*1000:.1f}ms), "
                        f"Step 2/3 events={ev_count} ({(t_ms1 - t_ms0)*1000:.1f}ms), "
                        f"total feature runtime: {(time.perf_counter() - t0)*1000:.1f}ms"
                    )
                elif sync_type == SyncType.LIVE_SYNC:
                    market_data_service.process_live_candle_swing(
                        source_id=source_id,
                        symbol=symbol,
                        timeframe=timeframe_str
                    )
                    market_data_service.process_live_market_structure(
                        source_id=source_id,
                        symbol=symbol,
                        timeframe=timeframe_str
                    )
                    logger.info(
                        f"[Diagnostic] Live Feature Engine finished for {symbol} ({timeframe_str}) "
                        f"in {(time.perf_counter() - t0)*1000:.1f}ms"
                    )
            except Exception as e:
                logger.exception(
                    f"[SyncOrchestrator] Feature Engine CRITICAL ERROR for {symbol} ({timeframe_str}): {e}"
                )
            finally:
                rebuild_manager.mark_completed(source_id, symbol, timeframe_str)

    def process_live_candle(self, payload: LiveCandlePayload, run_features: bool = True) -> dict:
        batch_payload = BatchCandlePayload(
            source_id=payload.source_id,
            symbol=payload.symbol,
            timeframe=payload.timeframe,
            sync_type=SyncType.LIVE_SYNC,
            payload_version=payload.payload_version,
            schema_version=payload.schema_version,
            candles=[payload.candle]
        )
        return self.process_candle_batch(batch_payload, run_features=run_features)

    def get_sync_status(self, source_id: str, symbol: str, timeframe: Timeframe) -> SyncStatusResponse:
        latest = candle_repo.get_latest_candle(source_id, symbol, timeframe.value)
        if not latest:
            return SyncStatusResponse(
                source_id=source_id,
                symbol=symbol,
                timeframe=timeframe,
                latest_candle_time_utc=None,
                latest_candle_epoch=None,
                status="NO_DATA"
            )

        # Check if background feature rebuild is currently executing
        is_rebuilding = rebuild_manager.is_rebuilding(source_id, symbol, timeframe.value)
        status_val = "REBUILDING" if is_rebuilding else "SYNCED"

        return SyncStatusResponse(
            source_id=source_id,
            symbol=symbol,
            timeframe=timeframe,
            latest_candle_time_utc=latest["candle_time_utc"],
            latest_candle_epoch=latest["candle_time_epoch"],
            status=status_val
        )

sync_orchestrator = SyncOrchestrator()
