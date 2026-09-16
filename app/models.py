from datetime import datetime
from typing import Optional
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func
)
from sqlalchemy.orm import relationship
from app.database import Base


class Device(Base):
    """Configured ZKTeco uFace800 biometric terminals."""
    __tablename__ = "devices"

    sn = Column(String(64), primary_key=True, index=True)
    site_name = Column(String(255), nullable=False)
    label = Column(String(255), nullable=False)
    last_seen_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return f"<Device sn={self.sn} label={self.label}>"


class RawDeviceRequest(Base):
    """Complete audit log of every raw inbound request received from any device or prober."""
    __tablename__ = "raw_device_requests"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    received_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    device_sn = Column(String(64), nullable=True, index=True)
    endpoint = Column(String(255), nullable=False)
    method = Column(String(10), nullable=False)
    query_params = Column(Text, nullable=True)
    headers = Column(Text, nullable=True)
    body = Column(Text, nullable=True)
    rejected = Column(Boolean, default=False, nullable=False, index=True)

    def __repr__(self) -> str:
        return f"<RawDeviceRequest id={self.id} sn={self.device_sn} endpoint={self.endpoint} rejected={self.rejected}>"


class ParseFailure(Base):
    """Stores unparseable raw ATTLOG lines defensively so no punch is ever dropped or lost."""
    __tablename__ = "parse_failures"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    received_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    device_sn = Column(String(64), nullable=False, index=True)
    raw_line = Column(Text, nullable=False)
    reason = Column(String(500), nullable=False)

    def __repr__(self) -> str:
        return f"<ParseFailure id={self.id} sn={self.device_sn} reason={self.reason}>"


class RawAttendanceEvent(Base):
    """Parsed attendance punches with idempotency guarantee across retries."""
    __tablename__ = "raw_attendance_events"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    device_sn = Column(String(64), nullable=False, index=True)
    pin = Column(String(64), nullable=False, index=True)
    event_dt = Column(DateTime, nullable=False, index=True)
    status_code = Column(String(32), nullable=True)
    verify_mode = Column(String(32), nullable=True)
    work_code = Column(String(32), nullable=True)
    raw_line = Column(Text, nullable=False)
    received_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("device_sn", "pin", "event_dt", name="uq_raw_attendance_event"),
    )

    sync_tasks = relationship("SyncQueue", back_populates="attendance_event", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<RawAttendanceEvent id={self.id} sn={self.device_sn} pin={self.pin} dt={self.event_dt}>"


class SyncQueue(Base):
    """Queue of attendance events to be synchronized to Odoo Online."""
    __tablename__ = "sync_queue"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    attendance_event_id = Column(
        BigInteger().with_variant(Integer, "sqlite"),
        ForeignKey("raw_attendance_events.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    device_sn = Column(String(64), nullable=False, index=True)
    pin = Column(String(64), nullable=False, index=True)
    event_dt = Column(DateTime, nullable=False)
    # status values: 'pending', 'synced', 'failed', 'unmatched', 'dead_letter'
    status = Column(String(32), default="pending", nullable=False, index=True)
    retry_count = Column(Integer, default=0, nullable=False)
    next_retry_at = Column(DateTime(timezone=True), nullable=True, index=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    attendance_event = relationship("RawAttendanceEvent", back_populates="sync_tasks")

    def __repr__(self) -> str:
        return f"<SyncQueue id={self.id} pin={self.pin} status={self.status} retries={self.retry_count}>"


class EmployeeMapping(Base):
    """Local cache of Odoo employee ID mapped to terminal PIN (barcode/badge)."""
    __tablename__ = "employee_mappings"

    pin = Column(String(64), primary_key=True, index=True)
    employee_id = Column(Integer, nullable=False)
    employee_name = Column(String(255), nullable=True)
    last_synced_at = Column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return f"<EmployeeMapping pin={self.pin} emp_id={self.employee_id} name={self.employee_name}>"
