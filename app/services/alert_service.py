import json
import logging
from typing import Any, Dict, List
import httpx
from app.config import settings

logger = logging.getLogger("relay.alerts")


def send_device_offline_alert(offline_devices: List[Dict[str, Any]]) -> bool:
    """
    Sends a generic webhook alert if ALERT_WEBHOOK_URL is configured.
    Works for Slack, Discord, Telegram-relay, and generic webhooks.
    """
    webhook_url = settings.ALERT_WEBHOOK_URL
    if not webhook_url:
        logger.debug("No ALERT_WEBHOOK_URL configured. Skipping alert dispatch.")
        return False

    dev_lines = []
    for d in offline_devices:
        last_seen = d.get("last_seen_at")
        last_seen_str = last_seen.isoformat() if last_seen else "Never"
        dev_lines.append(f"• Device SN: {d['sn']} ({d['label']} at {d['site_name']}) - Last seen: {last_seen_str}")

    message_text = (
        f"🚨 [ALERT] {len(offline_devices)} ZKTeco Biometric Terminal(s) Offline!\n"
        f"Threshold: {settings.HEARTBEAT_MINUTES} minutes without check-in.\n"
        + "\n".join(dev_lines)
    )

    payload = {
        # 'text' for Slack and generic hooks
        "text": message_text,
        # 'content' for Discord
        "content": message_text,
        "event_type": "device_heartbeat_timeout",
        "offline_count": len(offline_devices),
        "devices": offline_devices
    }

    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.post(webhook_url, json=payload)
            if response.status_code >= 400:
                logger.error("Failed to send webhook alert. HTTP %s: %s", response.status_code, response.text)
                return False
            logger.info("Successfully dispatched offline alert for %s device(s)", len(offline_devices))
            return True
    except Exception as exc:
        logger.error("Exception occurred while posting alert to %s: %s", webhook_url, exc)
        return False
