from datetime import datetime
from app.services.attlog_parser import parse_attlog_line, parse_attlog_payload


def test_tab_delimited_parsing():
    """Asserts that standard tab-delimited ATTLOG lines parse correctly."""
    body = (
        "1001\t2026-09-16 08:30:00\t0\t1\t0\n"
        "1002\t2026-09-16 08:31:15\t1\t15\t0\t0\n"
        "00345\t2026-09-16 08:32:00\t0\t1\n"
    )
    successful, failures = parse_attlog_payload(body)

    assert len(failures) == 0, f"Expected 0 failures, got: {failures}"
    assert len(successful) == 3

    # Check record 1
    assert successful[0].pin == "1001"
    assert successful[0].event_dt == datetime(2026, 9, 16, 8, 30, 0)
    assert successful[0].status_code == "0"
    assert successful[0].verify_mode == "1"

    # Check record 2 (with extra fields)
    assert successful[1].pin == "1002"
    assert successful[1].event_dt == datetime(2026, 9, 16, 8, 31, 15)
    assert successful[1].status_code == "1"
    assert successful[1].verify_mode == "15"

    # Check record 3 (preserving leading zeros on PIN)
    assert successful[2].pin == "00345"
    assert successful[2].event_dt == datetime(2026, 9, 16, 8, 32, 0)


def test_space_delimited_parsing():
    """Asserts that whitespace-delimited ATTLOG lines parse correctly."""
    body = (
        "1001 2026-09-16 08:30:00 0 1 0\n"
        "1002 2026-09-16 08:31:15 1 1 0\n"
        "9999 2026-09-16 17:45:22\n"
    )
    successful, failures = parse_attlog_payload(body)

    assert len(failures) == 0, f"Expected 0 failures, got: {failures}"
    assert len(successful) == 3

    assert successful[0].pin == "1001"
    assert successful[0].event_dt == datetime(2026, 9, 16, 8, 30, 0)
    assert successful[0].status_code == "0"
    assert successful[0].verify_mode == "1"

    assert successful[1].pin == "1002"
    assert successful[1].event_dt == datetime(2026, 9, 16, 8, 31, 15)

    assert successful[2].pin == "9999"
    assert successful[2].event_dt == datetime(2026, 9, 16, 17, 45, 22)


def test_malformed_attlog_lines_captured():
    """Asserts that unparseable lines are caught with failure reasons rather than dropped silently."""
    body = (
        "1001\t2026-09-16 08:30:00\t0\t1\t0\n"
        "GARBAGE_PAYLOAD_WITHOUT_DELIMITERS\n"
        "1002\tINVALID_TIMESTAMP\t0\n"
        "1003\t2026-09-16 08:35:00\t0\t1\t0\n"
    )
    successful, failures = parse_attlog_payload(body)

    assert len(successful) == 2
    assert len(failures) == 2

    failed_lines = [f[0] for f in failures]
    assert "GARBAGE_PAYLOAD_WITHOUT_DELIMITERS" in failed_lines
    assert "1002\tINVALID_TIMESTAMP\t0" in failed_lines
