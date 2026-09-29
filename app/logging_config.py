import json
import logging
import os
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from typing import Any, Dict


class JSONFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects for stdout structured logging."""

    def format(self, record: logging.LogRecord) -> str:
        log_data: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        # Include extra fields if provided
        for key, value in record.__dict__.items():
            if key not in {
                "args", "asctime", "created", "exc_info", "exc_text", "filename",
                "funcName", "id", "levelname", "levelno", "lineno", "module",
                "msecs", "message", "msg", "name", "pathname", "process",
                "processName", "relativeCreated", "stack_info", "thread", "threadName"
            } and not key.startswith("_"):
                log_data[key] = value

        return json.dumps(log_data, default=str)


# Logger for app-wide messages
logger = logging.getLogger("relay")

# Dedicated logger for raw inbound requests
raw_request_logger = logging.getLogger("relay.raw_requests")


def setup_logging(log_level: str = "INFO", log_dir: str = "logs") -> None:
    """Configures structured JSON logging to stdout and rotating file log for raw requests."""
    # Ensure serverless environments default to /tmp if relative path given
    if os.environ.get("VERCEL") and not log_dir.startswith("/tmp"):
        log_dir = "/tmp/logs"

    # Root / App logging to stdout in JSON format
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level.upper())

    # Clear existing handlers to avoid duplicates
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(JSONFormatter())
    root_logger.addHandler(stdout_handler)

    # Setup dedicated rotating file handler for raw device requests
    try:
        os.makedirs(log_dir, exist_ok=True)
        raw_log_file = os.path.join(log_dir, "raw_inbound_requests.log")
        file_handler = RotatingFileHandler(
            raw_log_file,
            maxBytes=10 * 1024 * 1024,  # 10 MB per file
            backupCount=10,             # Keep 10 backups
            encoding="utf-8"
        )
        file_handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s"
        ))
        raw_request_logger.setLevel(logging.INFO)
        raw_request_logger.addHandler(file_handler)
        raw_request_logger.propagate = False
    except OSError as exc:
        # On read-only or restricted serverless filesystems, fall back safely to stdout
        raw_request_logger.setLevel(logging.INFO)
        raw_request_logger.addHandler(stdout_handler)
        raw_request_logger.propagate = True
