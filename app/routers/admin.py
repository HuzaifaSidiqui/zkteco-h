import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.config import settings
from app.database import get_db
from app.models import Device, EmployeeMapping, ParseFailure, SyncQueue
from app.services.odoo_client import odoo_client

logger = logging.getLogger("relay.admin")

router = APIRouter(prefix="/admin", tags=["Admin & Observability"])


def verify_admin_auth(
    request: Request,
    authorization: Optional[str] = Header(None),
    token: Optional[str] = Query(None)
) -> bool:
    """
    Validates admin token from either:
    1. 'Authorization: Bearer <ADMIN_TOKEN>' header
    2. '?token=<ADMIN_TOKEN>' query parameter
    """
    expected = settings.ADMIN_TOKEN.strip()
    provided: Optional[str] = None

    if authorization and authorization.startswith("Bearer "):
        provided = authorization[len("Bearer "):].strip()
    elif token:
        provided = token.strip()

    if not provided or provided != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized: Invalid or missing admin token",
            headers={"WWW-Authenticate": "Bearer"}
        )
    return True


@router.get("/status")
def get_admin_status(
    db: Session = Depends(get_db),
    authorized: bool = Depends(verify_admin_auth)
) -> Dict[str, Any]:
    """
    JSON summary of device health, sync queue counts, and unmatched PINs.
    """
    now_utc = datetime.now(timezone.utc)
    cutoff_heartbeat = now_utc - timedelta(minutes=settings.HEARTBEAT_MINUTES)
    last_24h = now_utc - timedelta(hours=24)

    # 1. Device status
    devices_info: List[Dict[str, Any]] = []
    all_devices = db.query(Device).all()
    for dev in all_devices:
        last_seen = dev.last_seen_at
        is_online = False
        mins_ago: Optional[float] = None

        if last_seen:
            # Ensure aware for comparison
            ls_aware = last_seen if last_seen.tzinfo else last_seen.replace(tzinfo=timezone.utc)
            is_online = ls_aware >= cutoff_heartbeat
            mins_ago = round((now_utc - ls_aware).total_seconds() / 60.0, 1)

        devices_info.append({
            "sn": dev.sn,
            "site_name": dev.site_name,
            "label": dev.label,
            "last_seen_at": last_seen.isoformat() if last_seen else None,
            "is_online": is_online,
            "minutes_since_last_seen": mins_ago
        })

    # 2. Sync queue counts over last 24h
    status_counts_24h = {"pending": 0, "synced": 0, "failed": 0, "unmatched": 0, "dead_letter": 0}
    rows_24h = (
        db.query(SyncQueue.status, func.count(SyncQueue.id))
        .filter(SyncQueue.created_at >= last_24h)
        .group_by(SyncQueue.status)
        .all()
    )
    for st, count in rows_24h:
        if st in status_counts_24h:
            status_counts_24h[st] = count

    # Total counts overall
    total_counts = {"pending": 0, "synced": 0, "failed": 0, "unmatched": 0, "dead_letter": 0}
    rows_total = db.query(SyncQueue.status, func.count(SyncQueue.id)).group_by(SyncQueue.status).all()
    for st, count in rows_total:
        if st in total_counts:
            total_counts[st] = count

    # 3. Unmatched PINs list
    unmatched_rows = (
        db.query(SyncQueue)
        .filter(SyncQueue.status == "unmatched")
        .order_by(SyncQueue.created_at.desc())
        .limit(20)
        .all()
    )
    unmatched_list = [
        {
            "id": u.id,
            "device_sn": u.device_sn,
            "pin": u.pin,
            "event_dt": u.event_dt.isoformat() if u.event_dt else None,
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "last_error": u.last_error
        }
        for u in unmatched_rows
    ]

    # 4. Parse failures count
    parse_failures_count = db.query(func.count(ParseFailure.id)).scalar() or 0
    cached_employees_count = db.query(func.count(EmployeeMapping.pin)).scalar() or 0

    return {
        "timestamp": now_utc.isoformat(),
        "heartbeat_threshold_minutes": settings.HEARTBEAT_MINUTES,
        "devices": devices_info,
        "sync_queue_last_24h": status_counts_24h,
        "sync_queue_total": total_counts,
        "unmatched_punches_sample": unmatched_list,
        "parse_failures_total": parse_failures_count,
        "cached_employees_count": cached_employees_count,
        "device_field_supported": odoo_client.device_field_supported,
        "odoo_url": settings.ODOO_URL,
        "odoo_db": settings.ODOO_DB
    }


@router.post("/refresh-employees")
def refresh_employees_endpoint(
    db: Session = Depends(get_db),
    authorized: bool = Depends(verify_admin_auth)
) -> Dict[str, Any]:
    """
    Manually triggers an immediate pull of all active employee PINs from Odoo Online.
    """
    try:
        mapping = odoo_client.refresh_employee_cache(db)
        return {
            "status": "success",
            "message": f"Successfully refreshed employee cache from Odoo.",
            "loaded_count": len(mapping),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    except Exception as exc:
        logger.error("Admin refresh-employees failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Odoo employee refresh failed: {exc}"
        )


@router.post("/flush-queue")
@router.get("/flush-queue")
def flush_queue_endpoint(
    authorized: bool = Depends(verify_admin_auth)
) -> Dict[str, Any]:
    """Manually triggers an immediate flush of the attendance sync queue to Odoo."""
    from app.services.sync_worker import flush_sync_queue
    try:
        processed = flush_sync_queue()
        return {
            "status": "success",
            "message": f"Processed {processed} queued attendance record(s).",
            "processed_count": processed,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    except Exception as exc:
        logger.error("Admin flush-queue failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Sync queue flush failed: {exc}"
        )


@router.get("", response_class=HTMLResponse)
def get_admin_dashboard(
    request: Request,
    token: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    authorized: bool = Depends(verify_admin_auth)
) -> HTMLResponse:
    """
    Minimal, high-visibility HTML dashboard showing terminal status,
    sync queue metrics, and unmatched PINs. Auto-refreshes every 30 seconds.
    """
    status_data = get_admin_status(db=db, authorized=True)
    devices = status_data["devices"]
    q24 = status_data["sync_queue_last_24h"]
    q_tot = status_data["sync_queue_total"]
    unmatched = status_data["unmatched_punches_sample"]

    # Render devices table rows
    device_rows = ""
    for d in devices:
        badge = (
            '<span style="background-color:#10b981;color:#fff;padding:3px 8px;border-radius:4px;font-weight:600;">ONLINE</span>'
            if d["is_online"]
            else '<span style="background-color:#ef4444;color:#fff;padding:3px 8px;border-radius:4px;font-weight:600;">OFFLINE</span>'
        )
        last_seen = d["last_seen_at"] or "Never"
        mins = f"{d['minutes_since_last_seen']}m ago" if d["minutes_since_last_seen"] is not None else "N/A"
        device_rows += f"""
        <tr>
            <td style="padding:10px;border-bottom:1px solid #e5e7eb;font-family:monospace;font-weight:600;">{d['sn']}</td>
            <td style="padding:10px;border-bottom:1px solid #e5e7eb;">{d['label']}</td>
            <td style="padding:10px;border-bottom:1px solid #e5e7eb;">{d['site_name']}</td>
            <td style="padding:10px;border-bottom:1px solid #e5e7eb;">{badge}</td>
            <td style="padding:10px;border-bottom:1px solid #e5e7eb;">{last_seen} ({mins})</td>
        </tr>
        """

    # Render unmatched punches rows
    unmatched_rows = ""
    if not unmatched:
        unmatched_rows = '<tr><td colspan="4" style="padding:12px;text-align:center;color:#6b7280;">No unmatched PINs found! All punches mapped cleanly.</td></tr>'
    else:
        for u in unmatched:
            unmatched_rows += f"""
            <tr>
                <td style="padding:8px;border-bottom:1px solid #fee2e2;font-family:monospace;font-weight:bold;color:#b91c1c;">{u['pin']}</td>
                <td style="padding:8px;border-bottom:1px solid #fee2e2;font-family:monospace;">{u['device_sn']}</td>
                <td style="padding:8px;border-bottom:1px solid #fee2e2;">{u['event_dt']}</td>
                <td style="padding:8px;border-bottom:1px solid #fee2e2;color:#6b7280;font-size:0.85em;">{u['last_error']}</td>
            </tr>
            """

    current_token = token or ""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="refresh" content="30">
    <title>ZKTeco to Odoo Relay Dashboard</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background-color: #f3f4f6; margin: 0; padding: 24px; color: #1f2937; }}
        .container {{ max-width: 1100px; margin: 0 auto; }}
        .header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; }}
        .title {{ font-size: 24px; font-weight: bold; color: #111827; }}
        .meta {{ font-size: 14px; color: #6b7280; }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 16px; margin-bottom: 24px; }}
        .card {{ background: #fff; padding: 18px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); border-top: 4px solid #3b82f6; }}
        .card.green {{ border-color: #10b981; }}
        .card.yellow {{ border-color: #f59e0b; }}
        .card.red {{ border-color: #ef4444; }}
        .card.purple {{ border-color: #8b5cf6; }}
        .card-val {{ font-size: 28px; font-weight: bold; margin-top: 8px; color: #111827; }}
        .card-label {{ font-size: 13px; font-weight: 500; color: #6b7280; text-transform: uppercase; }}
        .card-sub {{ font-size: 12px; color: #9ca3af; margin-top: 4px; }}
        .section {{ background: #fff; border-radius: 8px; padding: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); margin-bottom: 24px; }}
        .section-title {{ font-size: 18px; font-weight: 600; margin-bottom: 16px; display: flex; justify-content: space-between; align-items: center; }}
        table {{ width: 100%; border-collapse: collapse; text-align: left; font-size: 14px; }}
        th {{ background-color: #f9fafb; padding: 10px; font-weight: 600; color: #4b5563; border-bottom: 1px solid #e5e7eb; }}
        .btn {{ background-color: #2563eb; color: #fff; border: none; padding: 8px 14px; border-radius: 6px; font-size: 13px; font-weight: 500; cursor: pointer; }}
        .btn:hover {{ background-color: #1d4ed8; }}
        .banner {{ background-color: #eff6ff; border-left: 4px solid #3b82f6; padding: 12px 16px; margin-bottom: 20px; border-radius: 4px; font-size: 14px; }}
    </style>
</head>
<body>
<div class="container">
    <div class="header">
        <div>
            <div class="title">ZKTeco uFace800 &rarr; Odoo Relay Status</div>
            <div class="meta">Server Time: {status_data['timestamp']} &bull; Auto-refreshing every 30s</div>
        </div>
        <div style="display:flex;gap:10px;">
            <button class="btn" style="background-color:#059669;" onclick="flushQueue()">Flush Sync Queue</button>
            <button class="btn" onclick="refreshEmployees()">Refresh Odoo Employees</button>
        </div>
    </div>

    <div class="banner">
        <strong>Odoo Connection:</strong> {status_data['odoo_url']} &bull; <strong>Database:</strong> {status_data['odoo_db']} &bull; <strong>Cached Employees:</strong> {status_data['cached_employees_count']} &bull; <strong>Device Field:</strong> {'Active (' + settings.ODOO_DEVICE_FIELD + ')' if status_data['device_field_supported'] else 'None (Skipped)'}
    </div>

    <!-- Metric Cards -->
    <div class="grid">
        <div class="card green">
            <div class="card-label">Synced (24h)</div>
            <div class="card-val">{q24['synced']}</div>
            <div class="card-sub">Total All Time: {q_tot['synced']}</div>
        </div>
        <div class="card">
            <div class="card-label">Pending Queue</div>
            <div class="card-val">{q_tot['pending']}</div>
            <div class="card-sub">Waiting worker dispatch</div>
        </div>
        <div class="card yellow">
            <div class="card-label">Failed Retries</div>
            <div class="card-val">{q_tot['failed']}</div>
            <div class="card-sub">Exponential backoff active</div>
        </div>
        <div class="card purple">
            <div class="card-label">Unmatched PINs</div>
            <div class="card-val">{q_tot['unmatched']}</div>
            <div class="card-sub">Requires Odoo mapping</div>
        </div>
        <div class="card red">
            <div class="card-label">Dead Letter</div>
            <div class="card-val">{q_tot['dead_letter']}</div>
            <div class="card-sub">&gt; 50 failed attempts</div>
        </div>
    </div>

    <!-- Devices Table -->
    <div class="section">
        <div class="section-title">Configured Biometric Terminals ({len(devices)})</div>
        <table>
            <thead>
                <tr>
                    <th>Serial Number</th>
                    <th>Label</th>
                    <th>Site Name</th>
                    <th>Status</th>
                    <th>Last Seen At</th>
                </tr>
            </thead>
            <tbody>
                {device_rows}
            </tbody>
        </table>
    </div>

    <!-- Unmatched PINs Table -->
    <div class="section">
        <div class="section-title">
            <span>Recent Unmatched Employee PINs</span>
            <span style="font-size:12px;font-weight:normal;color:#6b7280;">Map these barcodes in Odoo to resume sync</span>
        </div>
        <table>
            <thead>
                <tr style="background:#fef2f2;">
                    <th>PIN (User ID)</th>
                    <th>Device SN</th>
                    <th>Event Time</th>
                    <th>Details</th>
                </tr>
            </thead>
            <tbody>
                {unmatched_rows}
            </tbody>
        </table>
    </div>
</div>

<script>
async function flushQueue() {{
    const token = "{current_token}";
    try {{
        const res = await fetch("/admin/flush-queue?token=" + encodeURIComponent(token), {{
            method: "POST"
        }});
        const data = await res.json();
        if (res.ok) {{
            alert("Success! " + data.message);
            window.location.reload();
        }} else {{
            alert("Error: " + (data.detail || JSON.stringify(data)));
        }}
    }} catch (e) {{
        alert("Failed to communicate with server: " + e);
    }}
}}

async function refreshEmployees() {{
    const token = "{current_token}";
    try {{
        const res = await fetch("/admin/refresh-employees?token=" + encodeURIComponent(token), {{
            method: "POST"
        }});
        const data = await res.json();
        if (res.ok) {{
            alert("Success! " + data.message + " Loaded: " + data.loaded_count);
            window.location.reload();
        }} else {{
            alert("Error: " + (data.detail || JSON.stringify(data)));
        }}
    }} catch (e) {{
        alert("Failed to communicate with server: " + e);
    }}
}}
</script>
</body>
</html>
"""
    return HTMLResponse(content=html, status_code=200)
