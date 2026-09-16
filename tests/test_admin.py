from unittest.mock import patch
from app.models import Device, SyncQueue


def test_admin_auth_required(client):
    """Asserts that admin endpoints reject unauthorized requests with HTTP 401."""
    # Missing token
    res1 = client.get("/admin/status")
    assert res1.status_code == 401

    # Invalid token
    res2 = client.get("/admin/status", headers={"Authorization": "Bearer invalid-token"})
    assert res2.status_code == 401

    # Invalid token via query param
    res3 = client.get("/admin?token=wrong-token")
    assert res3.status_code == 401


def test_admin_status_json_success(client, db_session):
    """Asserts that GET /admin/status with valid token returns comprehensive status JSON."""
    token = "test-admin-token-123"
    res = client.get("/admin/status", headers={"Authorization": f"Bearer {token}"})

    assert res.status_code == 200
    data = res.json()

    assert "devices" in data
    assert "sync_queue_last_24h" in data
    assert "sync_queue_total" in data
    assert len(data["devices"]) >= 2
    # Verify device structure
    d0 = data["devices"][0]
    assert "sn" in d0
    assert "label" in d0
    assert "is_online" in d0


def test_admin_dashboard_html(client):
    """Asserts that GET /admin?token=... returns the HTML dashboard."""
    token = "test-admin-token-123"
    res = client.get(f"/admin?token={token}")

    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    assert "ZKTeco uFace800" in res.text
    assert "Refresh Odoo Employees" in res.text


def test_admin_refresh_employees_endpoint(client, monkeypatch):
    """Asserts that POST /admin/refresh-employees invokes cache refresh successfully."""
    token = "test-admin-token-123"
    with patch("app.services.odoo_client.odoo_client.refresh_employee_cache", return_value={"1001": 42}):
        res = client.post("/admin/refresh-employees", headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == 200
        assert res.json()["status"] == "success"
        assert res.json()["loaded_count"] == 1
