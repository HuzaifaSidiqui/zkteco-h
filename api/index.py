import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from app.main import app
except Exception as e:
    from fastapi import FastAPI
    from fastapi.responses import PlainTextResponse

    err_trace = traceback.format_exc()
    print(f"CRITICAL_STARTUP_ERROR:\n{err_trace}", file=sys.stderr)

    app = FastAPI()

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD"])
    def catch_all_error(path: str):
        return PlainTextResponse(
            f"--- Vercel FastAPI Startup Error ---\n\n{err_trace}",
            status_code=500
        )
