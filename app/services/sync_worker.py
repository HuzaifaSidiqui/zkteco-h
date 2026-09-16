import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from sqlalchemy import or_
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models import SyncQueue
from app.services.odoo_client import odoo_client

logger = logging.getLogger("relay.worker")

BACKOFF_STEPS = [30, 60, 120, 300, 900]  # 30s, 1m, 2m, 5m, 15m (capped at 15m)
MAX_RETRIES = 50


def calculate_next_retry(retry_count: int) -> datetime:
    """Calculates exponential backoff delay based on retry attempt number."""
    idx = min(max(0, retry_count - 1), len(BACKOFF_STEPS) - 1)
    seconds = BACKOFF_STEPS[idx]
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


def process_event_record(item_id: int) -> bool:
    """
    Processes a single sync_queue row within its own database session.
    Returns True if successfully synced or moved to terminal state (unmatched/dead_letter),
    Returns False if an XML-RPC error occurred (meaning subsequent punches for this PIN should pause).
    """
    with SessionLocal() as db:
        item = db.query(SyncQueue).filter(SyncQueue.id == item_id).first()
        if not item or item.status not in ("pending", "failed"):
            return True

        # 1. Resolve employee mapping
        employee_id = odoo_client.get_employee_id(item.pin, db)
        if not employee_id:
            logger.warning(
                "SyncQueue %s: PIN '%s' does not match any known employee in Odoo. Marking as 'unmatched'.",
                item.id, item.pin
            )
            item.status = "unmatched"
            item.last_error = f"PIN '{item.pin}' not matched to any employee barcode/id in Odoo"
            item.updated_at = datetime.now(timezone.utc)
            db.commit()
            return True

        # 2. Synchronize to Odoo Online
        try:
            odoo_client.sync_attendance_event(
                employee_id=employee_id,
                event_dt=item.event_dt,
                device_sn=item.device_sn
            )
            item.status = "synced"
            item.last_error = None
            item.next_retry_at = None
            item.updated_at = datetime.now(timezone.utc)
            db.commit()
            logger.info("Successfully synced punch (Queue ID: %s, PIN: %s, DT: %s)", item.id, item.pin, item.event_dt)
            return True

        except Exception as exc:
            new_retry_count = item.retry_count + 1
            item.retry_count = new_retry_count
            item.last_error = str(exc)
            item.updated_at = datetime.now(timezone.utc)

            if new_retry_count >= MAX_RETRIES:
                item.status = "dead_letter"
                item.next_retry_at = None
                logger.error(
                    "SyncQueue %s: PIN '%s' reached max retries (%s). Moved to 'dead_letter'. Error: %s",
                    item.id, item.pin, MAX_RETRIES, exc
                )
            else:
                item.status = "failed"
                item.next_retry_at = calculate_next_retry(new_retry_count)
                logger.warning(
                    "SyncQueue %s: PIN '%s' failed attempt %s. Next retry at %s. Error: %s",
                    item.id, item.pin, new_retry_count, item.next_retry_at, exc
                )

            db.commit()
            return False


def process_employee_queue(pin: str, item_ids: List[int]) -> None:
    """
    Processes all pending events for a single employee in strict chronological order.
    If an event encounters an XML-RPC failure, aborts processing remaining events for this employee.
    """
    for item_id in item_ids:
        success = process_event_record(item_id)
        if not success:
            logger.info("Halting further sync for PIN '%s' until current failure is retried.", pin)
            break


def flush_sync_queue(max_workers: int = 5) -> int:
    """
    Background worker function called periodically (every 60s).
    Pulls pending and retryable failed rows, groups by employee PIN,
    and dispatches across a thread pool while preserving per-employee order.
    """
    now_utc = datetime.now(timezone.utc)
    with SessionLocal() as db:
        eligible_items = (
            db.query(SyncQueue.id, SyncQueue.pin, SyncQueue.event_dt)
            .filter(
                or_(
                    SyncQueue.status == "pending",
                    (SyncQueue.status == "failed") & (SyncQueue.next_retry_at <= now_utc)
                )
            )
            .order_by(SyncQueue.event_dt.asc(), SyncQueue.id.asc())
            .limit(500)
            .all()
        )

    if not eligible_items:
        return 0

    logger.info("Found %s queued attendance punch(es) ready for sync.", len(eligible_items))

    # Group by PIN preserving chronological order
    pin_to_item_ids: Dict[str, List[int]] = {}
    for item_id, pin, _ in eligible_items:
        pin_to_item_ids.setdefault(pin, []).append(item_id)

    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="odoo-sync") as executor:
        futures = [
            executor.submit(process_employee_queue, pin, ids)
            for pin, ids in pin_to_item_ids.items()
        ]
        for f in futures:
            try:
                f.result()
            except Exception as e:
                logger.error("Unexpected worker exception during queue processing: %s", e)

    return len(eligible_items)
