import xmlrpc.client
from datetime import datetime, timezone
from unittest.mock import MagicMock
from app.models import EmployeeMapping, RawAttendanceEvent, SyncQueue
from app.services.odoo_client import odoo_client
from app.services.sync_worker import MAX_RETRIES, process_event_record


def test_odoo_xmlrpc_failure_marks_failed_with_backoff(db_session, monkeypatch):
    """
    Simulates an Odoo XML-RPC failure and asserts the event lands in
    sync_queue as status=failed with incremented retry count rather than being lost.
    """
    # 1. Setup an employee mapping
    emp = EmployeeMapping(pin="1001", employee_id=42, employee_name="Jane Doe")
    db_session.add(emp)

    # 2. Add an attendance event and sync_queue entry
    event = RawAttendanceEvent(
        device_sn="KNOWN_SN_001",
        pin="1001",
        event_dt=datetime(2026, 9, 16, 8, 30, 0),
        raw_line="1001\t2026-09-16 08:30:00\t0\t1\t0"
    )
    db_session.add(event)
    db_session.flush()

    queue_item = SyncQueue(
        attendance_event_id=event.id,
        device_sn="KNOWN_SN_001",
        pin="1001",
        event_dt=datetime(2026, 9, 16, 8, 30, 0),
        status="pending",
        retry_count=0
    )
    db_session.add(queue_item)
    db_session.commit()

    # 3. Mock odoo_client.sync_attendance_event to raise an XML-RPC fault
    def mock_sync_failure(*args, **kwargs):
        raise xmlrpc.client.Fault(500, "Simulated Odoo Connection Timeout / Server Error")

    monkeypatch.setattr(odoo_client, "sync_attendance_event", mock_sync_failure)
    monkeypatch.setattr(odoo_client, "get_employee_id", lambda pin, db: 42)

    # 4. Process record
    result = process_event_record(queue_item.id)
    assert result is False  # XML-RPC error returned False

    # 5. Verify row in database
    db_session.expire_all()
    updated_item = db_session.query(SyncQueue).filter(SyncQueue.id == queue_item.id).first()
    assert updated_item is not None
    assert updated_item.status == "failed"
    assert updated_item.retry_count == 1
    assert updated_item.next_retry_at is not None
    # Compare timezone-aware
    now = datetime.now(timezone.utc)
    next_retry = updated_item.next_retry_at
    if next_retry.tzinfo is None:
        next_retry = next_retry.replace(tzinfo=timezone.utc)
    assert next_retry > now
    assert "Simulated Odoo Connection Timeout" in updated_item.last_error


def test_unmatched_pin_marks_status_unmatched(db_session, monkeypatch):
    """Asserts that events with unknown PINs are marked 'unmatched' and not endlessly retried."""
    event = RawAttendanceEvent(
        device_sn="KNOWN_SN_001",
        pin="UNKNOWN_PIN_9999",
        event_dt=datetime(2026, 9, 16, 9, 0, 0),
        raw_line="UNKNOWN_PIN_9999\t2026-09-16 09:00:00\t0\t1\t0"
    )
    db_session.add(event)
    db_session.flush()

    queue_item = SyncQueue(
        attendance_event_id=event.id,
        device_sn="KNOWN_SN_001",
        pin="UNKNOWN_PIN_9999",
        event_dt=datetime(2026, 9, 16, 9, 0, 0),
        status="pending",
        retry_count=0
    )
    db_session.add(queue_item)
    db_session.commit()

    # Mock employee resolution to return None
    monkeypatch.setattr(odoo_client, "get_employee_id", lambda pin, db: None)

    result = process_event_record(queue_item.id)
    assert result is True  # Resolved to terminal state

    db_session.expire_all()
    updated_item = db_session.query(SyncQueue).filter(SyncQueue.id == queue_item.id).first()
    assert updated_item is not None
    assert updated_item.status == "unmatched"
    assert "not matched to any employee" in updated_item.last_error


def test_check_in_then_check_out_logic(db_session, monkeypatch):
    """Asserts that first punch creates check-in, and subsequent punch updates check-out."""
    odoo_mock = MagicMock()
    monkeypatch.setattr(odoo_client, "execute_kw", odoo_mock)
    monkeypatch.setattr(odoo_client, "probe_device_field", lambda: True)

    employee_id = 77
    t1 = datetime(2026, 9, 16, 8, 0, 0)
    t2 = datetime(2026, 9, 16, 17, 0, 0)

    # 1. First punch: No open attendance record found
    odoo_mock.side_effect = [
        [],        # search_read open_records -> empty
        101        # create -> returns new attendance id 101
    ]
    res1 = odoo_client.sync_attendance_event(employee_id, t1, "KNOWN_SN_001")
    assert res1["action"] == "check_in"
    assert res1["attendance_id"] == 101

    # Verify create was called with check_in
    create_call = odoo_mock.call_args_list[1]
    assert create_call[0][0] == "hr.attendance"
    assert create_call[0][1] == "create"
    vals = create_call[0][2][0]
    assert vals["employee_id"] == 77
    assert vals["check_in"] == "2026-09-16 08:00:00"

    # 2. Second punch: Open attendance record found with check_in = 08:00:00
    odoo_mock.reset_mock()
    odoo_mock.side_effect = [
        [{"id": 101, "check_in": "2026-09-16 08:00:00"}],  # search_read open_records
        True                                                # write -> True
    ]
    res2 = odoo_client.sync_attendance_event(employee_id, t2, "KNOWN_SN_001")
    assert res2["action"] == "check_out"
    assert res2["attendance_id"] == 101

    write_call = odoo_mock.call_args_list[1]
    assert write_call[0][0] == "hr.attendance"
    assert write_call[0][1] == "write"
    write_args = write_call[0][2]
    assert write_args[0] == [101]
    assert write_args[1]["check_out"] == "2026-09-16 17:00:00"


def test_max_retries_moves_to_dead_letter(db_session, monkeypatch):
    """Asserts that 50 failed attempts moves record to dead_letter status."""
    event = RawAttendanceEvent(
        device_sn="KNOWN_SN_001",
        pin="1001",
        event_dt=datetime(2026, 9, 16, 8, 30, 0),
        raw_line="1001\t2026-09-16 08:30:00"
    )
    db_session.add(event)
    db_session.flush()

    queue_item = SyncQueue(
        attendance_event_id=event.id,
        device_sn="KNOWN_SN_001",
        pin="1001",
        event_dt=datetime(2026, 9, 16, 8, 30, 0),
        status="failed",
        retry_count=49  # next failure will be 50
    )
    db_session.add(queue_item)
    db_session.commit()

    monkeypatch.setattr(odoo_client, "get_employee_id", lambda pin, db: 1)
    monkeypatch.setattr(
        odoo_client,
        "sync_attendance_event",
        MagicMock(side_effect=Exception("Repeated Odoo RPC connection drop"))
    )

    process_event_record(queue_item.id)

    db_session.expire_all()
    updated_item = db_session.query(SyncQueue).filter(SyncQueue.id == queue_item.id).first()
    assert updated_item is not None
    assert updated_item.status == "dead_letter"
    assert updated_item.retry_count == 50
    assert updated_item.next_retry_at is None
