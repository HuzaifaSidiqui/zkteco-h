import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from app.config import settings
from app.database import SessionLocal
from app.models import Device
from app.services.alert_service import send_device_offline_alert
from app.services.odoo_client import odoo_client
from app.services.sync_worker import flush_sync_queue

logger = logging.getLogger("relay.scheduler")

scheduler = BackgroundScheduler()


def job_flush_sync_queue() -> None:
    """Runs every 60 seconds to consume pending items and retry failed items."""
    try:
        count = flush_sync_queue()
        if count > 0:
            logger.info("Scheduler: Processed %s items from sync queue.", count)
    except Exception as exc:
        logger.error("Scheduler error during sync queue flush: %s", exc)


def job_check_device_heartbeat() -> None:
    """
    Runs every 15 minutes.
    Checks if any configured device has not been seen within HEARTBEAT_MINUTES.
    Dispatches alert if offline devices are found.
    """
    logger.info("Scheduler: Running device heartbeat check...")
    now_utc = datetime.now(timezone.utc)
    threshold_dt = now_utc - timedelta(minutes=settings.HEARTBEAT_MINUTES)

    offline_devices: List[Dict[str, Any]] = []

    with SessionLocal() as db:
        devices = db.query(Device).all()
        for dev in devices:
            is_offline = False
            if dev.last_seen_at is None:
                is_offline = True
            else:
                # Ensure dev.last_seen_at is timezone-aware for comparison
                last_seen = dev.last_seen_at
                if last_seen.tzinfo is None:
                    last_seen = last_seen.replace(tzinfo=timezone.utc)
                if last_seen < threshold_dt:
                    is_offline = True

            if is_offline:
                offline_devices.append({
                    "sn": dev.sn,
                    "label": dev.label,
                    "site_name": dev.site_name,
                    "last_seen_at": dev.last_seen_at
                })

    if offline_devices:
        logger.warning("Heartbeat check detected %s offline device(s)!", len(offline_devices))
        send_device_offline_alert(offline_devices)
    else:
        logger.info("All configured devices are healthy and reporting within threshold.")


def job_refresh_employee_cache() -> None:
    """Runs every 10 minutes to refresh Odoo employee PIN mappings."""
    try:
        with SessionLocal() as db:
            odoo_client.refresh_employee_cache(db)
    except Exception as exc:
        logger.warning("Scheduled refresh of employee PIN cache failed: %s", exc)


def start_scheduler() -> None:
    """Starts the APScheduler with configured intervals."""
    if scheduler.running:
        return

    # 1. Sync queue flush: every 60 seconds
    scheduler.add_job(
        job_flush_sync_queue,
        trigger=IntervalTrigger(seconds=60),
        id="flush_sync_queue",
        name="Flush Odoo Sync Queue",
        replace_existing=True
    )

    # 2. Device heartbeat: every 15 minutes
    scheduler.add_job(
        job_check_device_heartbeat,
        trigger=IntervalTrigger(minutes=15),
        id="check_device_heartbeat",
        name="Device Heartbeat Check",
        replace_existing=True
    )

    # 3. Employee cache refresh: every 10 minutes
    scheduler.add_job(
        job_refresh_employee_cache,
        trigger=IntervalTrigger(minutes=10),
        id="refresh_employee_cache",
        name="Refresh Employee Cache",
        replace_existing=True
    )

    scheduler.start()
    logger.info("Background scheduler started successfully.")


def stop_scheduler() -> None:
    """Gracefully shuts down the APScheduler."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Background scheduler shut down.")
