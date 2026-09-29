import os
import sys
import traceback
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

# Ensure root directory is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from app.main import app
except Exception:
    err_msg = traceback.format_exc()
    print("FATAL_STARTUP_CRASH:\n" + err_msg, file=sys.stderr)
    app = FastAPI(title="Cold Start Diagnostic Fallback")

    @app.api_route("/{rest_of_path:path}", methods=["GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS", "PATCH"])
    def catch_all(rest_of_path: str):
        return PlainTextResponse(f"Startup Failure:\n\n{err_msg}", status_code=500)
