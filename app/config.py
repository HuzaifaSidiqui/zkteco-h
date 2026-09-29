import json
import logging
import os
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, ValidationInfo, field_validator
from pydantic_core import PydanticUndefined
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("relay.config")


class DeviceConfig(BaseModel):
    sn: str
    site_name: str = "Default Site"
    label: str = "uFace800 Terminal"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # App & Ingress
    DOMAIN: str = "relay.example.com"
    LOG_LEVEL: str = "INFO"
    LOG_DIR: str = "/tmp/logs" if os.environ.get("VERCEL") else "logs"
    ADMIN_TOKEN: str = "change-me-super-secret-admin-token-12345"

    # Database
    DATABASE_URL: str = "postgresql://relay_user:relay_pass@db:5432/relay_db"

    # Odoo Connection
    ODOO_URL: str = "https://example.odoo.com"
    ODOO_DB: str = "example_db"
    ODOO_USERNAME: str = "admin@example.com"
    ODOO_PASSWORD: str = "odoo_api_key_or_password"
    ODOO_EMPLOYEE_PIN_FIELD: str = "barcode"
    ODOO_DEVICE_FIELD: str = "x_zk_device_sn"

    # Heartbeat & Alerting
    ALERT_WEBHOOK_URL: Optional[str] = None
    HEARTBEAT_MINUTES: int = 15

    # Device List (JSON string or list of dicts)
    # Default 5 seed devices for uFace800
    DEVICES: str = Field(
        default=json.dumps([
            {"sn": "UF800000001", "site_name": "Headquarters - Main Entrance", "label": "HQ Entrance uFace800"},
            {"sn": "UF800000002", "site_name": "Headquarters - Warehouse", "label": "HQ Warehouse uFace800"},
            {"sn": "UF800000003", "site_name": "Branch Office 1", "label": "Branch 1 Front Desk"},
            {"sn": "UF800000004", "site_name": "Branch Office 2", "label": "Branch 2 Staff Gate"},
            {"sn": "UF800000005", "site_name": "Factory Workshop", "label": "Factory Floor Gate"},
        ])
    )

    # iClock Registration / Options defaults (configurable per requirements)
    ICLOCK_STAMP: str = "9999"
    ICLOCK_OPSTAMP: str = "9999"
    ICLOCK_ERROR_DELAY: str = "60"
    ICLOCK_DELAY: str = "30"
    ICLOCK_TRANS_TIMES: str = "00:00;23:59"
    ICLOCK_TRANS_INTERVAL: str = "1"
    ICLOCK_TRANS_FLAG: str = "1111000000"
    ICLOCK_REALTIME: str = "1"
    ICLOCK_ENCRYPT: str = "0"

    # Terminal Timezone (terminals typically send naive local time YYYY-MM-DD HH:MM:SS)
    DEVICE_TIMEZONE: str = "UTC"

    @field_validator("*", mode="before")
    @classmethod
    def sanitize_empty_strings(cls, v: Any, info: ValidationInfo) -> Any:
        """Coerces empty or whitespace-only environment variable strings to field defaults."""
        if isinstance(v, str) and not v.strip():
            field_name = info.field_name
            if field_name:
                field = cls.model_fields.get(field_name)
                if field and field.default is not PydanticUndefined:
                    return field.default
                return None
        return v

    @property
    def parsed_devices(self) -> List[DeviceConfig]:
        """Parses the DEVICES string into DeviceConfig objects."""
        raw_str = (self.DEVICES or "").strip()
        if (raw_str.startswith("'") and raw_str.endswith("'")) or (raw_str.startswith('"') and raw_str.endswith('"')):
            raw_str = raw_str[1:-1].strip()
        try:
            raw = json.loads(raw_str)
            if isinstance(raw, list):
                return [DeviceConfig(**item) for item in raw]
            return []
        except Exception:
            # Fallback simple split if plain comma-separated serials given
            serials = [s.strip(" '\"") for s in raw_str.split(",") if s.strip(" '\"")]
            return [DeviceConfig(sn=s, site_name="Site", label=f"Terminal {s}") for s in serials]

    @property
    def allowed_device_sns(self) -> set[str]:
        return {d.sn for d in self.parsed_devices}


try:
    settings = Settings()
except Exception as exc:
    logger.error("Failed to initialize Settings from environment (%s). Using fallback defaults.", exc)
    settings = Settings(_env_file=None, HEARTBEAT_MINUTES=15)
