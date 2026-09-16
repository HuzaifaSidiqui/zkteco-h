import os
import sys
from unittest.mock import patch
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

# Ensure project root is in path
sys.path.insert(0, os.path.realpath(os.path.join(os.path.dirname(__file__), "..")))

TEST_DB_PATH = os.path.join(os.path.dirname(__file__), "test_relay.sqlite")
TEST_DB_URL = f"sqlite:///{TEST_DB_PATH}"

os.environ["DATABASE_URL"] = TEST_DB_URL
os.environ["ADMIN_TOKEN"] = "test-admin-token-123"
os.environ["DEVICES"] = '[{"sn": "KNOWN_SN_001", "site_name": "Test Site 1", "label": "Test Device 1"}, {"sn": "KNOWN_SN_002", "site_name": "Test Site 2", "label": "Test Device 2"}]'

import app.database as app_db
import app.services.sync_worker as sync_worker_mod
from app.config import settings
from app.database import Base, get_db
import app.models
from app.main import app

# Create file-based SQLite test engine
test_engine = create_engine(
    TEST_DB_URL,
    connect_args={"check_same_thread": False}
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

# Point database engines and SessionLocals to test_engine
app_db.engine = test_engine
app_db.SessionLocal = TestingSessionLocal
sync_worker_mod.SessionLocal = TestingSessionLocal


@pytest.fixture(autouse=True)
def disable_scheduler():
    """Disables background scheduler threads during test runs to avoid background race conditions."""
    with patch("app.main.start_scheduler"), patch("app.main.stop_scheduler"):
        yield


@pytest.fixture(scope="function")
def db_session():
    """Creates a clean fresh database schema for every test function."""
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)
    session = TestingSessionLocal()

    # Seed known test devices
    from app.models import Device
    session.add(Device(sn="KNOWN_SN_001", site_name="Test Site 1", label="Test Device 1"))
    session.add(Device(sn="KNOWN_SN_002", site_name="Test Site 2", label="Test Device 2"))
    session.commit()

    yield session

    session.close()
    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture(scope="function")
def client(db_session):
    """FastAPI TestClient with overridden database dependency."""
    def override_get_db():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
