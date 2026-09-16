"""Initial database schema

Revision ID: 0001_initial_schema
Revises: 
Create Date: 2026-09-16 20:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. devices
    op.create_table(
        "devices",
        sa.Column("sn", sa.String(length=64), nullable=False),
        sa.Column("site_name", sa.String(length=255), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("sn")
    )
    op.create_index(op.f("ix_devices_sn"), "devices", ["sn"], unique=False)

    # 2. raw_device_requests
    op.create_table(
        "raw_device_requests",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("device_sn", sa.String(length=64), nullable=True),
        sa.Column("endpoint", sa.String(length=255), nullable=False),
        sa.Column("method", sa.String(length=10), nullable=False),
        sa.Column("query_params", sa.Text(), nullable=True),
        sa.Column("headers", sa.Text(), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("rejected", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.PrimaryKeyConstraint("id")
    )
    op.create_index(op.f("ix_raw_device_requests_device_sn"), "raw_device_requests", ["device_sn"], unique=False)
    op.create_index(op.f("ix_raw_device_requests_received_at"), "raw_device_requests", ["received_at"], unique=False)
    op.create_index(op.f("ix_raw_device_requests_rejected"), "raw_device_requests", ["rejected"], unique=False)

    # 3. parse_failures
    op.create_table(
        "parse_failures",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("device_sn", sa.String(length=64), nullable=False),
        sa.Column("raw_line", sa.Text(), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.PrimaryKeyConstraint("id")
    )
    op.create_index(op.f("ix_parse_failures_device_sn"), "parse_failures", ["device_sn"], unique=False)
    op.create_index(op.f("ix_parse_failures_received_at"), "parse_failures", ["received_at"], unique=False)

    # 4. raw_attendance_events
    op.create_table(
        "raw_attendance_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("device_sn", sa.String(length=64), nullable=False),
        sa.Column("pin", sa.String(length=64), nullable=False),
        sa.Column("event_dt", sa.DateTime(), nullable=False),
        sa.Column("status_code", sa.String(length=32), nullable=True),
        sa.Column("verify_mode", sa.String(length=32), nullable=True),
        sa.Column("work_code", sa.String(length=32), nullable=True),
        sa.Column("raw_line", sa.Text(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("device_sn", "pin", "event_dt", name="uq_raw_attendance_event")
    )
    op.create_index(op.f("ix_raw_attendance_events_device_sn"), "raw_attendance_events", ["device_sn"], unique=False)
    op.create_index(op.f("ix_raw_attendance_events_event_dt"), "raw_attendance_events", ["event_dt"], unique=False)
    op.create_index(op.f("ix_raw_attendance_events_pin"), "raw_attendance_events", ["pin"], unique=False)

    # 5. sync_queue
    op.create_table(
        "sync_queue",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("attendance_event_id", sa.BigInteger(), nullable=False),
        sa.Column("device_sn", sa.String(length=64), nullable=False),
        sa.Column("pin", sa.String(length=64), nullable=False),
        sa.Column("event_dt", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["attendance_event_id"], ["raw_attendance_events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id")
    )
    op.create_index(op.f("ix_sync_queue_attendance_event_id"), "sync_queue", ["attendance_event_id"], unique=False)
    op.create_index(op.f("ix_sync_queue_created_at"), "sync_queue", ["created_at"], unique=False)
    op.create_index(op.f("ix_sync_queue_device_sn"), "sync_queue", ["device_sn"], unique=False)
    op.create_index(op.f("ix_sync_queue_next_retry_at"), "sync_queue", ["next_retry_at"], unique=False)
    op.create_index(op.f("ix_sync_queue_pin"), "sync_queue", ["pin"], unique=False)
    op.create_index(op.f("ix_sync_queue_status"), "sync_queue", ["status"], unique=False)

    # 6. employee_mappings
    op.create_table(
        "employee_mappings",
        sa.Column("pin", sa.String(length=64), nullable=False),
        sa.Column("employee_id", sa.Integer(), nullable=False),
        sa.Column("employee_name", sa.String(length=255), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("pin")
    )
    op.create_index(op.f("ix_employee_mappings_pin"), "employee_mappings", ["pin"], unique=False)


def downgrade() -> None:
    op.drop_table("employee_mappings")
    op.drop_table("sync_queue")
    op.drop_table("raw_attendance_events")
    op.drop_table("parse_failures")
    op.drop_table("raw_device_requests")
    op.drop_table("devices")
