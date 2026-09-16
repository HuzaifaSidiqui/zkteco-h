from datetime import datetime
from app.models import Device, ParseFailure, RawAttendanceEvent, RawDeviceRequest, SyncQueue


def test_unknown_sn_rejected_but_logged(client, db_session):
    """
    Asserts that requests with unknown serial numbers:
    1. Still receive HTTP 200 'OK' (not revealing validity to probers).
    2. Are recorded in raw_device_requests with rejected=True.
    3. Are NOT processed into raw_attendance_events or sync_queue.
    """
    unknown_sn = "MALICIOUS_OR_UNKNOWN_SN_999"
    attlog_body = "1001\t2026-09-16 08:30:00\t0\t1\t0\n"

    response = client.post(f"/iclock/cdata?SN={unknown_sn}&table=ATTLOG", content=attlog_body)

    assert response.status_code == 200
    assert "OK" in response.text

    # Verify raw_device_requests logged the attempt as rejected
    logged = db_session.query(RawDeviceRequest).filter(RawDeviceRequest.device_sn == unknown_sn).first()
    assert logged is not None
    assert logged.rejected is True
    assert "1001" in logged.body

    # Verify no punches were stored or queued
    att_count = db_session.query(RawAttendanceEvent).count()
    queue_count = db_session.query(SyncQueue).count()
    assert att_count == 0
    assert queue_count == 0


def test_get_cdata_registration_handshake(client, db_session):
    """Asserts that known SN receives the standard iClock registration option text block."""
    known_sn = "KNOWN_SN_001"
    response = client.get(f"/iclock/cdata?SN={known_sn}&options=all&pushver=2.4.1")

    assert response.status_code == 200
    assert f"GET OPTION FROM: {known_sn}" in response.text
    assert "Stamp=" in response.text
    assert "Delay=30" in response.text
    assert "Realtime=1" in response.text

    # Assert last_seen_at was updated
    dev = db_session.query(Device).filter(Device.sn == known_sn).first()
    assert dev.last_seen_at is not None


def test_post_attlog_enqueues_and_is_idempotent(client, db_session):
    """Asserts that ATTLOG punches are enqueued into sync_queue and resends are idempotent no-ops."""
    known_sn = "KNOWN_SN_001"
    payload = (
        "1001\t2026-09-16 09:00:00\t0\t1\t0\n"
        "1002\t2026-09-16 09:01:00\t0\t1\t0\n"
    )

    # First push
    res1 = client.post(f"/iclock/cdata?SN={known_sn}&table=ATTLOG", content=payload)
    assert res1.status_code == 200
    assert "OK" in res1.text

    events = db_session.query(RawAttendanceEvent).all()
    queue_items = db_session.query(SyncQueue).all()
    assert len(events) == 2
    assert len(queue_items) == 2
    assert all(q.status == "pending" for q in queue_items)

    # Second push (identical device retry)
    res2 = client.post(f"/iclock/cdata?SN={known_sn}&table=ATTLOG", content=payload)
    assert res2.status_code == 200
    assert "OK" in res2.text

    # Ensure no duplicates were inserted
    events_after = db_session.query(RawAttendanceEvent).all()
    queue_after = db_session.query(SyncQueue).all()
    assert len(events_after) == 2
    assert len(queue_after) == 2


def test_attlog_with_malformed_lines_saves_failures(client, db_session):
    """Asserts that bad lines in ATTLOG are captured in parse_failures table while valid lines still succeed."""
    known_sn = "KNOWN_SN_001"
    mixed_payload = (
        "1001\t2026-09-16 09:00:00\t0\t1\t0\n"
        "MALFORMED_LINE_CORRUPT_BYTES\n"
    )
    res = client.post(f"/iclock/cdata?SN={known_sn}&table=ATTLOG", content=mixed_payload)
    assert res.status_code == 200

    events = db_session.query(RawAttendanceEvent).all()
    failures = db_session.query(ParseFailure).all()
    assert len(events) == 1
    assert len(failures) == 1
    assert failures[0].device_sn == known_sn
    assert "MALFORMED_LINE_CORRUPT_BYTES" in failures[0].raw_line


def test_getrequest_and_devicecmd_endpoints(client, db_session):
    """Asserts that /iclock/getrequest and /iclock/devicecmd return plain text OK."""
    known_sn = "KNOWN_SN_001"
    res_get = client.get(f"/iclock/getrequest?SN={known_sn}")
    assert res_get.status_code == 200
    assert "OK" in res_get.text

    res_cmd = client.post(f"/iclock/devicecmd?SN={known_sn}", content="ID=1&Return=0&CMD=DATA")
    assert res_cmd.status_code == 200
    assert "OK" in res_cmd.text
