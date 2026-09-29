import logging
import os
from typing import Generator
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from sqlalchemy.pool import NullPool
from app.config import settings

logger = logging.getLogger("relay.database")


def resolve_database_url(raw_url: str) -> tuple[str, bool]:
    """Normalizes database URL and selects driver based on runtime environment."""
    url = raw_url.strip()
    is_serverless = bool(os.environ.get("VERCEL") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))

    # If running on Vercel and the URL still points to local docker container 'db:5432',
    # fall back to a local sqlite db to prevent a 30s TCP connect timeout hanging cold starts.
    if is_serverless and "@db:5432" in url:
        logger.warning(
            "Default docker hostname 'db:5432' detected in serverless environment without custom DATABASE_URL configured. "
            "Using temporary SQLite database to avoid network timeout."
        )
        return "sqlite:////tmp/relay.db", False

    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]

    requires_ssl = False
    # Convert standard postgresql:// to pure-Python pg8000 driver
    if url.startswith("postgresql://"):
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        # Remove parameters not accepted by pg8000 connect()
        sslmode_list = qs.pop("sslmode", [])
        sslmode = sslmode_list[0] if sslmode_list else None
        if sslmode in ("require", "prefer", "verify-ca", "verify-full") or "neon.tech" in parsed.netloc:
            requires_ssl = True
        qs.pop("channel_binding", None)
        new_query = urlencode(qs, doseq=True)
        url = urlunparse((
            "postgresql+pg8000",
            parsed.netloc,
            parsed.path,
            parsed.params,
            new_query,
            parsed.fragment
        ))
        logger.info("Configured pure-Python pg8000 driver for PostgreSQL connection (ssl=%s).", requires_ssl)

    return url, requires_ssl


db_url, requires_ssl = resolve_database_url(settings.DATABASE_URL)
is_sqlite = db_url.startswith("sqlite")
is_serverless = bool(os.environ.get("VERCEL") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))

if is_sqlite:
    connect_args = {"check_same_thread": False}
elif "pg8000" in db_url:
    connect_args = {"timeout": 10}
    if requires_ssl:
        import ssl
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
        connect_args["ssl_context"] = ssl_ctx
else:
    connect_args = {"connect_timeout": 10}

pool_kwargs = {}
if not is_sqlite:
    if is_serverless:
        # Serverless environments benefit from NullPool to avoid stale connections across cold starts
        pool_kwargs = {"poolclass": NullPool}
    else:
        pool_kwargs = {
            "pool_pre_ping": True,
            "pool_size": 10,
            "max_overflow": 20
        }

try:
    engine = create_engine(
        db_url,
        connect_args=connect_args,
        **pool_kwargs
    )
except Exception as exc:
    logger.error("Failed to initialize database engine with URL '%s': %s. Falling back to temporary SQLite.", db_url, exc)
    engine = create_engine("sqlite:////tmp/fallback.db", connect_args={"check_same_thread": False})

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
