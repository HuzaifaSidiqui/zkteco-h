import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from app.config import settings
from app.database import SessionLocal, init_db
from app.logging_config import setup_logging
from app.routers import admin, iclock
from app.services.odoo_client import odoo_client
from app.services.scheduler import start_scheduler, stop_scheduler

# Initialize structured logging
setup_logging(log_level=settings.LOG_LEVEL, log_dir=settings.LOG_DIR)
logger = logging.getLogger("relay.main")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan context for startup initialization and graceful shutdown."""
    logger.info("Initializing Attendance Relay Service for domain: %s", settings.DOMAIN)

    # 1. Initialize database schema & seed devices
    init_db()

    # 2. Preload cached employee mappings from DB
    with SessionLocal() as db:
        odoo_client.load_cached_employees(db)

    # 3. Probe Odoo connection and device field (non-blocking if Odoo is offline)
    try:
        odoo_client.probe_device_field()
        with SessionLocal() as db:
            odoo_client.refresh_employee_cache(db)
    except Exception as exc:
        logger.warning(
            "Initial Odoo handshake/cache load deferred (Odoo may be currently unreachable): %s. "
            "Relay will continue accepting terminal punches and sync once Odoo is online.",
            exc
        )

    # 4. Start background scheduler
    start_scheduler()

    logger.info("Relay service startup completed. Ready for terminal connections.")
    yield

    # Shutdown
    logger.info("Shutting down Attendance Relay Service...")
    stop_scheduler()
    logger.info("Relay service shutdown complete.")


app = FastAPI(
    title="ZKTeco uFace800 to Odoo Attendance Relay",
    version="1.0.0",
    description="High-reliability ADMS push relay service syncing biometric attendance punches to Odoo Online.",
    lifespan=lifespan
)

# Mount routers
app.include_router(iclock.router)
app.include_router(admin.router)


@app.get("/health", tags=["Health"])
def health_check() -> JSONResponse:
    """Service health probe for Docker / orchestrator healthchecks."""
    return JSONResponse(
        content={
            "status": "healthy",
            "service": "zkteco-odoo-relay",
            "version": "1.0.0"
        },
        status_code=200
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)
