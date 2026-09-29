import os
import sys
import traceback
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_app = None
_error = None

try:
    from app.main import app as _app
except Exception:
    _error = traceback.format_exc()

if _app is not None:
    app = _app
else:
    app = FastAPI(title="Diagnostic Fallback")

    @app.api_route("/{rest_of_path:path}", methods=["GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS", "PATCH"])
    def catch_all(rest_of_path: str):
        return PlainTextResponse(
            f"=== PYTHON STARTUP EXCEPTION ===\n\n{_error}",
            status_code=500
        )
