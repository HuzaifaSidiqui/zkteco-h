import json
import logging
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import PlainTextResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.config import settings
from app.database import get_db
from app.logging_config import raw_request_logger
from app.models import Device, ParseFailure, RawAttendanceEvent, RawDeviceRequest, SyncQueue
from app.services.attlog_parser import parse_attlog_payload

logger = logging.getLogger("relay.iclock")

router = APIRouter(prefix="/iclock", tags=["ZKTeco Device Protocol"])


def log_and_verify_request(
    request: Request,
    raw_body_bytes: bytes,
    db: Session,
    endpoint_name: str
) -> tuple[bool, Optional[str]]:
    """
    Common helper for all /iclock/* endpoints:
    1. Extracts SN parameter (case-insensitive query param).
    2. Logs the unmodified request (headers, query, body) to raw_device_requests and rotating file.
    3. Verifies if SN belongs to the configured devices list.
    4. If authorized, updates Device.last_seen_at = now().
    5. Returns (is_authorized, sn).
    """
    # Extract SN from query params (check 'SN' or 'sn')
    sn = request.query_params.get("SN") or request.query_params.get("sn")
    if sn:
        sn = sn.strip()

    body_str = raw_body_bytes.decode("utf-8", errors="replace")
    headers_dict = dict(request.headers)
    query_str = str(request.query_params)

    # Check if SN is in configured allowed list
    is_authorized = bool(sn and sn in settings.allowed_device_sns)

    # 1. Log to rotating file
    raw_request_logger.info(
        "METHOD=%s ENDPOINT=%s SN=%s AUTHORIZED=%s QUERY=%s HEADERS=%s BODY=%s",
        request.method,
        endpoint_name,
        sn,
        is_authorized,
        query_str,
        json.dumps(headers_dict),
        body_str
    )

    # 2. Persist to raw_device_requests table
    raw_req_record = RawDeviceRequest(
        device_sn=sn,
        endpoint=endpoint_name,
        method=request.method,
        query_params=query_str,
        headers=json.dumps(headers_dict),
        body=body_str,
        rejected=not is_authorized
    )
    db.add(raw_req_record)

    # 3. If authorized, update last_seen_at
    if is_authorized and sn:
        device = db.query(Device).filter(Device.sn == sn).first()
        if device:
            device.last_seen_at = datetime.now(timezone.utc)
        else:
            # Upsert device entry if somehow missing
            device = Device(sn=sn, site_name="Configured Terminal", label=f"Device {sn}", last_seen_at=datetime.now(timezone.utc))
            db.add(device)

    db.commit()

    if not is_authorized:
        logger.warning("Rejected unauthorized request from SN '%s' at endpoint '%s'", sn, endpoint_name)

    return is_authorized, sn


@router.get("/cdata", response_class=PlainTextResponse)
async def get_cdata(
    request: Request,
    db: Session = Depends(get_db)
) -> Response:
    """
    Device registration / handshake endpoint.
    GET /iclock/cdata?SN=<serial>&options=all&pushver=...
    """
    raw_body = await request.body()
    is_authorized, sn = log_and_verify_request(request, raw_body, db, "/iclock/cdata")

    # If unauthorized, return 200 OK without revealing invalidity or sending config
    if not is_authorized or not sn:
        return PlainTextResponse("OK\n", status_code=200)

    # Return standard iClock registration/option configuration block
    config_response = (
        f"GET OPTION FROM: {sn}\n"
        f"Stamp={settings.ICLOCK_STAMP}\n"
        f"OpStamp={settings.ICLOCK_OPSTAMP}\n"
        f"ErrorDelay={settings.ICLOCK_ERROR_DELAY}\n"
        f"Delay={settings.ICLOCK_DELAY}\n"
        f"TransTimes={settings.ICLOCK_TRANS_TIMES}\n"
        f"TransInterval={settings.ICLOCK_TRANS_INTERVAL}\n"
        f"TransFlag={settings.ICLOCK_TRANS_FLAG}\n"
        f"Realtime={settings.ICLOCK_REALTIME}\n"
        f"Encrypt={settings.ICLOCK_ENCRYPT}\n"
    )
    return PlainTextResponse(config_response, status_code=200)


@router.post("/cdata", response_class=PlainTextResponse)
async def post_cdata(
    request: Request,
    db: Session = Depends(get_db)
) -> Response:
    """
    Device data push endpoint.
    POST /iclock/cdata?SN=<serial>&table=ATTLOG&Stamp=...
    Handles ATTLOG, OPERLOG, USERINFO/USER.
    """
    raw_body = await request.body()
    is_authorized, sn = log_and_verify_request(request, raw_body, db, "/iclock/cdata")

    # If unauthorized, respond 200 OK without processing payload
    if not is_authorized or not sn:
        return PlainTextResponse("OK\n", status_code=200)

    table = request.query_params.get("table", "").upper()
    body_text = raw_body.decode("utf-8", errors="replace")

    if table == "ATTLOG":
        successful_records, parse_failures = parse_attlog_payload(body_text)

        # 1. Log parse failures defensively into parse_failures table
        for raw_line, reason in parse_failures:
            fail_record = ParseFailure(
                device_sn=sn,
                raw_line=raw_line,
                reason=reason
            )
            db.add(fail_record)

        # 2. Insert successful records and enqueue new ones
        for item in successful_records:
            # Check if this exact punch already exists
            existing = (
                db.query(RawAttendanceEvent)
                .filter(
                    RawAttendanceEvent.device_sn == sn,
                    RawAttendanceEvent.pin == item.pin,
                    RawAttendanceEvent.event_dt == item.event_dt
                )
                .first()
            )
            if existing:
                # Duplicate resend by device - harmless no-op
                continue

            event_record = RawAttendanceEvent(
                device_sn=sn,
                pin=item.pin,
                event_dt=item.event_dt,
                status_code=item.status_code,
                verify_mode=item.verify_mode,
                work_code=item.work_code,
                raw_line=item.raw_line
            )
            db.add(event_record)
            try:
                db.flush()  # Obtain event_record.id
                # Enqueue into sync_queue
                queue_item = SyncQueue(
                    attendance_event_id=event_record.id,
                    device_sn=sn,
                    pin=item.pin,
                    event_dt=item.event_dt,
                    status="pending",
                    retry_count=0,
                    next_retry_at=datetime.now(timezone.utc)
                )
                db.add(queue_item)
            except IntegrityError:
                db.rollback()
                logger.debug("Harmless concurrent duplicate punch for SN %s, PIN %s, DT %s", sn, item.pin, item.event_dt)

        db.commit()
        logger.info(
            "Processed ATTLOG from %s: %s parsed, %s failures stored",
            sn, len(successful_records), len(parse_failures)
        )

    # For table=OPERLOG, table=USERINFO/USER or other tables,
    # raw_device_requests already captured the entire body. No further processing needed.

    # Always return immediate plain-text "OK"
    return PlainTextResponse("OK\n", status_code=200)


@router.get("/getrequest", response_class=PlainTextResponse)
async def get_request(
    request: Request,
    db: Session = Depends(get_db)
) -> Response:
    """
    Device polling for pending server commands.
    GET /iclock/getrequest?SN=<serial>
    We do not push commands in this relay build, so always respond "OK".
    """
    raw_body = await request.body()
    log_and_verify_request(request, raw_body, db, "/iclock/getrequest")
    return PlainTextResponse("OK\n", status_code=200)


@router.post("/devicecmd", response_class=PlainTextResponse)
async def post_device_cmd(
    request: Request,
    db: Session = Depends(get_db)
) -> Response:
    """
    Device command execution acknowledgements.
    POST /iclock/devicecmd?SN=<serial>
    """
    raw_body = await request.body()
    log_and_verify_request(request, raw_body, db, "/iclock/devicecmd")
    return PlainTextResponse("OK\n", status_code=200)
