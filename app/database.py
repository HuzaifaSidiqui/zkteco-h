import logging
from typing import Generator
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from app.config import settings

logger = logging.getLogger("relay.database")

# Handle SQLite vs PostgreSQL engine arguments
is_sqlite = settings.DATABASE_URL.startswith("sqlite")

connect_args = {"check_same_thread": False} if is_sqlite else {}
pool_kwargs = {} if is_sqlite else {
    "pool_pre_ping": True,
    "pool_size": 10,
    "max_overflow": 20
}

engine = create_engine(
    settings.DATABASE_URL,
    connect_args=connect_args,
    **pool_kwargs
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def seed_devices(db: Session) -> None:
    """Upserts configured devices from settings into the devices table on startup."""
    from app.models import Device
    for item in settings.parsed_devices:
        dev = db.query(Device).filter(Device.sn == item.sn).first()
        if not dev:
            dev = Device(
                sn=item.sn,
                site_name=item.site_name,
                label=item.label,
                last_seen_at=None
            )
            db.add(dev)
            logger.info("Seeded new device into database: %s (%s)", item.sn, item.label)
        else:
            dev.site_name = item.site_name
            dev.label = item.label
    db.commit()


def init_db() -> None:
    """Creates tables if they do not exist and seeds devices."""
    import app.models  # Ensure models are loaded before create_all
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        seed_devices(db)
