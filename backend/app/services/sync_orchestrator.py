from typing import Optional
from backend.app.models.market_data import BatchCandlePayload, LiveCandlePayload, SyncStatusResponse, CandleItem
from backend.app.services.timezone_service import verify_timestamp_consistency
from backend.app.services.session_resolver import get_primary_session
from backend.app.services.data_quality_service import validate_candle_sanity, detect_timeline_gaps
from backend.app.repositories.candle_repo import candle_repo
from backend.app.repositories.symbol_repo import symbol_repo
from backend.app.core.constants import Timeframe, SyncType

class SyncOrchestrator:
    def process_candle_batch(self, payload: BatchCandlePayload) -> dict:
        source_id = payload.source_id
        symbol = payload.symbol
        timeframe_str = payload.timeframe.value

        # Fetch symbol metadata to verify precision if registered
        sym_meta = symbol_repo.get_symbol(source_id, symbol)
        expected_digits = sym_meta["digits"] if sym_meta else None

        # 1. Validation: Timestamp consistency & Bar sanity
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

        # 4. Idempotent Upsert
        inserted_count = candle_repo.upsert_candles(prepared_rows)

        return {
            "status": "SUCCESS",
            "source_id": source_id,
            "symbol": symbol,
            "timeframe": timeframe_str,
            "processed_count": inserted_count,
            "detected_gaps_count": len(gaps),
            "sync_type": payload.sync_type.value
        }

    def process_live_candle(self, payload: LiveCandlePayload) -> dict:
        batch_payload = BatchCandlePayload(
            source_id=payload.source_id,
            symbol=payload.symbol,
            timeframe=payload.timeframe,
            sync_type=SyncType.LIVE_SYNC,
            payload_version=payload.payload_version,
            schema_version=payload.schema_version,
            candles=[payload.candle]
        )
        return self.process_candle_batch(batch_payload)

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

        return SyncStatusResponse(
            source_id=source_id,
            symbol=symbol,
            timeframe=timeframe,
            latest_candle_time_utc=latest["candle_time_utc"],
            latest_candle_epoch=latest["candle_time_epoch"],
            status="SYNCED"
        )

sync_orchestrator = SyncOrchestrator()
