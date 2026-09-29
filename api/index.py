import os
import sys
import traceback
from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse

app = FastAPI(title="ZKTeco Relay")

@app.get("/health")
def health_check():
    return JSONResponse(
        content={
            "status": "healthy",
            "service": "zkteco-odoo-relay",
            "version": "1.0.0"
        },
        status_code=200
    )

@app.get("/debug")
def debug_probe():
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    results = {}
    modules_to_test = [
        "app.config",
        "app.database",
        "app.models",
        "app.services.odoo_client",
        "app.services.attlog_parser",
        "app.routers.iclock",
        "app.routers.admin",
        "app.main"
    ]
    for mod in modules_to_test:
        try:
            __import__(mod)
            results[mod] = "OK"
        except Exception:
            results[mod] = traceback.format_exc()
            return JSONResponse(content={"error_at": mod, "results": results}, status_code=500)

    return JSONResponse(content={"status": "all_imports_passed", "modules": results}, status_code=200)
