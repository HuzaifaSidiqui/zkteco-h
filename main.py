import os
import sys
import traceback
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

# Ensure root directory is in sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from app.main import app
except Exception:
    err_traceback = traceback.format_exc()
    print("FATAL_STARTUP_ERROR:\n" + err_traceback, file=sys.stderr)

    app = FastAPI(title="Relay Cold-Start Diagnostic Fallback")

    @app.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD", "PATCH"])
    def diagnostic_fallback(full_path: str):
        return PlainTextResponse(
            f"=== VERCEL STARTUP FAILURE ===\n\n{err_traceback}",
            status_code=500
        )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
