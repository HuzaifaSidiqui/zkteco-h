import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Tuple

logger = logging.getLogger("relay.parser")

DATETIME_REGEX = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}$")


@dataclass
class ParsedAttendanceRecord:
    pin: str
    event_dt: datetime
    status_code: Optional[str] = None
    verify_mode: Optional[str] = None
    work_code: Optional[str] = None
    raw_line: str = ""


def try_parse_dt(dt_candidate: str) -> Optional[datetime]:
    """Tries parsing a string in ISO/standard formats (YYYY-MM-DD HH:MM:SS or with T)."""
    cleaned = dt_candidate.strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%d-%m-%Y %H:%M:%S", "%d/%m/%Y %H:%M:%S"):
        try:
            return datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
    return None


def parse_attlog_line(line: str) -> Tuple[Optional[ParsedAttendanceRecord], Optional[str]]:
    """
    Defensively parses a single ATTLOG line.
    Tries tab-delimited first, then whitespace-delimited.
    Returns: (ParsedAttendanceRecord, None) on success, or (None, failure_reason) on error.
    """
    cleaned_line = line.strip()
    if not cleaned_line:
        return None, "Empty line"

    # 1. Try Tab-delimited
    tab_parts = [p.strip() for p in cleaned_line.split("\t") if p.strip()]
    if len(tab_parts) >= 2:
        pin = tab_parts[0]
        # Check if tab_parts[1] is full datetime or just date
        dt = try_parse_dt(tab_parts[1])
        if dt:
            status_code = tab_parts[2] if len(tab_parts) > 2 else None
            verify_mode = tab_parts[3] if len(tab_parts) > 3 else None
            work_code = tab_parts[4] if len(tab_parts) > 4 else None
            return ParsedAttendanceRecord(
                pin=pin,
                event_dt=dt,
                status_code=status_code,
                verify_mode=verify_mode,
                work_code=work_code,
                raw_line=cleaned_line
            ), None
        elif len(tab_parts) >= 3:
            # Maybe tab_parts[1] is Date and tab_parts[2] is Time
            combined_dt = f"{tab_parts[1]} {tab_parts[2]}"
            dt = try_parse_dt(combined_dt)
            if dt:
                status_code = tab_parts[3] if len(tab_parts) > 3 else None
                verify_mode = tab_parts[4] if len(tab_parts) > 4 else None
                work_code = tab_parts[5] if len(tab_parts) > 5 else None
                return ParsedAttendanceRecord(
                    pin=pin,
                    event_dt=dt,
                    status_code=status_code,
                    verify_mode=verify_mode,
                    work_code=work_code,
                    raw_line=cleaned_line
                ), None

    # 2. Fall back to Whitespace-delimited
    space_parts = cleaned_line.split()
    if len(space_parts) >= 3:
        pin = space_parts[0]
        # Standard format when space-separated is: PIN Date Time [Status] [VerifyMode] [WorkCode]
        combined_dt = f"{space_parts[1]} {space_parts[2]}"
        dt = try_parse_dt(combined_dt)
        if dt:
            status_code = space_parts[3] if len(space_parts) > 3 else None
            verify_mode = space_parts[4] if len(space_parts) > 4 else None
            work_code = space_parts[5] if len(space_parts) > 5 else None
            return ParsedAttendanceRecord(
                pin=pin,
                event_dt=dt,
                status_code=status_code,
                verify_mode=verify_mode,
                work_code=work_code,
                raw_line=cleaned_line
            ), None

    # 3. Fallback: Search for any datetime pattern in the entire line using regex
    match = re.search(r"(\d{4}[-/]\d{2}[-/]\d{2}[ T]\d{2}:\d{2}:\d{2})", cleaned_line)
    if match:
        dt_str = match.group(1)
        dt = try_parse_dt(dt_str)
        if dt:
            # Everything before datetime candidate is considered PIN
            before = cleaned_line[:match.start()].strip()
            # If before has whitespace/tabs, take the first/last token
            pin_tokens = re.split(r"[\s\t,]+", before)
            if pin_tokens and pin_tokens[0]:
                pin = pin_tokens[0]
                return ParsedAttendanceRecord(
                    pin=pin,
                    event_dt=dt,
                    raw_line=cleaned_line
                ), None

    reason = f"Could not extract PIN and DateTime from line: '{cleaned_line}'"
    return None, reason


def parse_attlog_payload(body: str) -> Tuple[List[ParsedAttendanceRecord], List[Tuple[str, str]]]:
    """
    Parses a multi-line ATTLOG request body.
    Returns:
        (successful_records, list_of_(failed_raw_line, reason))
    """
    successful: List[ParsedAttendanceRecord] = []
    failures: List[Tuple[str, str]] = []

    lines = body.splitlines()
    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped:
            continue

        record, failure_reason = parse_attlog_line(stripped)
        if record:
            successful.append(record)
        else:
            logger.warning("ATTLOG line parse failed: %s (Reason: %s)", stripped, failure_reason)
            failures.append((stripped, failure_reason or "Unknown parse error"))

    return successful, failures
