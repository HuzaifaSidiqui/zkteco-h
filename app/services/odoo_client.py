import logging
import ssl
import xmlrpc.client
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo
from sqlalchemy.orm import Session
from app.config import settings
from app.models import EmployeeMapping

logger = logging.getLogger("relay.odoo")


class OdooClient:
    """Wrapper for Odoo XML-RPC communication with cached authentication and employee mapping."""

    def __init__(self) -> None:
        self.url = settings.ODOO_URL.rstrip("/")
        self.db = settings.ODOO_DB
        self.username = settings.ODOO_USERNAME
        self.password = settings.ODOO_PASSWORD
        self.uid: Optional[int] = None
        self.device_field_supported: Optional[bool] = None
        self._in_memory_employees: Dict[str, int] = {}
        self._common_proxy: Optional[xmlrpc.client.ServerProxy] = None
        self._object_proxy: Optional[xmlrpc.client.ServerProxy] = None

    def _get_common_proxy(self) -> xmlrpc.client.ServerProxy:
        if self._common_proxy is None:
            self._common_proxy = xmlrpc.client.ServerProxy(
                f"{self.url}/xmlrpc/2/common",
                allow_none=True
            )
        return self._common_proxy

    def _get_object_proxy(self) -> xmlrpc.client.ServerProxy:
        if self._object_proxy is None:
            self._object_proxy = xmlrpc.client.ServerProxy(
                f"{self.url}/xmlrpc/2/object",
                allow_none=True
            )
        return self._object_proxy

    def authenticate(self, force: bool = False) -> int:
        """Authenticates with Odoo XML-RPC common endpoint and caches the UID."""
        if self.uid and not force:
            return self.uid

        logger.info("Authenticating with Odoo XML-RPC at %s (DB: %s, User: %s)", self.url, self.db, self.username)
        common = self._get_common_proxy()
        uid = common.authenticate(self.db, self.username, self.password, {})
        if not uid:
            logger.error("Authentication to Odoo failed for user %s on database %s", self.username, self.db)
            raise ConnectionError(f"Failed to authenticate with Odoo Online ({self.url}). Check credentials.")

        self.uid = int(uid)
        logger.info("Successfully authenticated with Odoo. Assigned UID: %s", self.uid)
        return self.uid

    def execute_kw(self, model: str, method: str, args: list, kwargs: Optional[dict] = None) -> Any:
        """Executes an RPC call against Odoo object endpoint with automatic re-authentication on auth failure."""
        uid = self.authenticate()
        models = self._get_object_proxy()
        call_kwargs = kwargs or {}

        try:
            return models.execute_kw(self.db, uid, self.password, model, method, args, call_kwargs)
        except xmlrpc.client.Fault as fault:
            # Fault 1 or AccessDenied implies authentication or session expiration
            if "AccessDenied" in str(fault) or "Access Denied" in str(fault) or fault.faultCode in (1, 3):
                logger.warning("Odoo XML-RPC auth fault (%s). Re-authenticating and retrying call...", fault)
                uid = self.authenticate(force=True)
                return models.execute_kw(self.db, uid, self.password, model, method, args, call_kwargs)
            raise

    def probe_device_field(self) -> bool:
        """
        Probes once at startup whether ODOO_DEVICE_FIELD exists on hr.attendance in this Odoo database.
        Returns True if supported, False otherwise.
        """
        if self.device_field_supported is not None:
            return self.device_field_supported

        try:
            fields_meta = self.execute_kw(
                "hr.attendance",
                "fields_get",
                [],
                {"attributes": ["type"]}
            )
            field_name = settings.ODOO_DEVICE_FIELD
            self.device_field_supported = field_name in fields_meta
            if self.device_field_supported:
                logger.info("Custom device field '%s' detected on hr.attendance. Device SN will be tracked.", field_name)
            else:
                logger.warning(
                    "Custom device field '%s' was NOT found on hr.attendance. Skipping setting it gracefully.",
                    field_name
                )
        except Exception as exc:
            logger.warning("Could not probe fields on hr.attendance (%s). Disabling custom device field.", exc)
            self.device_field_supported = False

        return self.device_field_supported

    def refresh_employee_cache(self, db: Session) -> Dict[str, int]:
        """
        Refreshes {pin: employee_id} mapping by calling hr.employee.search_read.
        Saves into local in-memory dict and employee_mappings table.
        """
        pin_field = settings.ODOO_EMPLOYEE_PIN_FIELD
        logger.info("Fetching employee PIN mappings from Odoo using field '%s'...", pin_field)

        try:
            records = self.execute_kw(
                "hr.employee",
                "search_read",
                [[[pin_field, "!=", False]]],
                {"fields": ["id", "name", pin_field]}
            )
        except Exception as exc:
            logger.error("Failed to fetch employee mappings from Odoo: %s", exc)
            raise

        new_mapping: Dict[str, int] = {}
        for rec in records:
            raw_pin = rec.get(pin_field)
            if raw_pin:
                pin = str(raw_pin).strip()
                emp_id = int(rec["id"])
                name = rec.get("name", "")
                new_mapping[pin] = emp_id

                # Upsert into database
                mapping_obj = db.query(EmployeeMapping).filter(EmployeeMapping.pin == pin).first()
                if not mapping_obj:
                    mapping_obj = EmployeeMapping(pin=pin, employee_id=emp_id, employee_name=name)
                    db.add(mapping_obj)
                else:
                    mapping_obj.employee_id = emp_id
                    mapping_obj.employee_name = name
                    mapping_obj.last_synced_at = datetime.now(timezone.utc)

        db.commit()
        self._in_memory_employees = new_mapping
        logger.info("Employee PIN cache updated successfully. Loaded %s active employee mappings.", len(new_mapping))
        return self._in_memory_employees

    def load_cached_employees(self, db: Session) -> None:
        """Loads cached mappings from DB into memory on startup."""
        mappings = db.query(EmployeeMapping).all()
        for m in mappings:
            self._in_memory_employees[m.pin] = m.employee_id
        logger.info("Loaded %s cached employee PIN mappings from database.", len(self._in_memory_employees))

    def get_employee_id(self, pin: str, db: Session) -> Optional[int]:
        """Finds employee ID for a given PIN. Checks in-memory, DB, and lastly Odoo search."""
        clean_pin = str(pin).strip()
        if clean_pin in self._in_memory_employees:
            return self._in_memory_employees[clean_pin]

        # Check DB
        mapping_obj = db.query(EmployeeMapping).filter(EmployeeMapping.pin == clean_pin).first()
        if mapping_obj:
            self._in_memory_employees[clean_pin] = mapping_obj.employee_id
            return mapping_obj.employee_id

        # Fallback: Query Odoo directly for this PIN
        pin_field = settings.ODOO_EMPLOYEE_PIN_FIELD
        try:
            records = self.execute_kw(
                "hr.employee",
                "search_read",
                [[[pin_field, "=", clean_pin]]],
                {"fields": ["id", "name"], "limit": 1}
            )
            if records:
                emp_id = int(records[0]["id"])
                name = records[0].get("name", "")
                self._in_memory_employees[clean_pin] = emp_id
                db.add(EmployeeMapping(pin=clean_pin, employee_id=emp_id, employee_name=name))
                db.commit()
                return emp_id
        except Exception as exc:
            logger.warning("On-demand Odoo lookup failed for PIN %s: %s", clean_pin, exc)

        return None

    def _normalize_to_utc_string(self, dt: datetime) -> str:
        """Converts device datetime to UTC and formats as 'YYYY-MM-DD HH:MM:SS' for Odoo."""
        try:
            if settings.DEVICE_TIMEZONE and settings.DEVICE_TIMEZONE.upper() != "UTC":
                tz = ZoneInfo(settings.DEVICE_TIMEZONE)
                if dt.tzinfo is None:
                    local_dt = dt.replace(tzinfo=tz)
                else:
                    local_dt = dt.astimezone(tz)
                utc_dt = local_dt.astimezone(ZoneInfo("UTC"))
                return utc_dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception as exc:
            logger.warning("Timezone conversion failed for %s (%s). Using raw dt.", dt, exc)

        return dt.strftime("%Y-%m-%d %H:%M:%S")

    def sync_attendance_event(
        self,
        employee_id: int,
        event_dt: datetime,
        device_sn: str
    ) -> Dict[str, Any]:
        """
        Synchronizes a single attendance punch to Odoo hr.attendance.
        If an open record exists (check_out = False) with check_in < event_dt:
          writes check_out on it.
        Otherwise:
          creates a new record with check_in = event_dt.
        Optionally sets ODOO_DEVICE_FIELD if supported.
        """
        dt_str = self._normalize_to_utc_string(event_dt)
        self.probe_device_field()

        # Query hr.attendance for existing record with check_out = False
        domain = [
            ("employee_id", "=", employee_id),
            ("check_out", "=", False)
        ]
        open_records = self.execute_kw(
            "hr.attendance",
            "search_read",
            [domain],
            {"fields": ["id", "check_in"], "limit": 1, "order": "check_in desc"}
        )

        device_field = settings.ODOO_DEVICE_FIELD
        track_device = self.device_field_supported and device_field

        if open_records:
            existing = open_records[0]
            existing_id = existing["id"]
            existing_check_in = str(existing.get("check_in", ""))

            # If check_in is earlier than this event's datetime, check it out
            if existing_check_in < dt_str:
                write_vals: Dict[str, Any] = {"check_out": dt_str}
                if track_device:
                    write_vals[device_field] = device_sn

                self.execute_kw("hr.attendance", "write", [[existing_id], write_vals])
                logger.info(
                    "Employee %s checked OUT at %s on record %s (Device: %s)",
                    employee_id, dt_str, existing_id, device_sn
                )
                return {"action": "check_out", "attendance_id": existing_id}

        # Otherwise create new check-in
        create_vals: Dict[str, Any] = {
            "employee_id": employee_id,
            "check_in": dt_str
        }
        if track_device:
            create_vals[device_field] = device_sn

        new_id = self.execute_kw("hr.attendance", "create", [create_vals])
        logger.info(
            "Employee %s checked IN at %s on new record %s (Device: %s)",
            employee_id, dt_str, new_id, device_sn
        )
        return {"action": "check_in", "attendance_id": new_id}


# Singleton client instance
odoo_client = OdooClient()
