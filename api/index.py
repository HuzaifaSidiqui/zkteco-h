import os
import sys
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app


@app.middleware("http")
async def restore_vercel_path_middleware(request: Request, call_next):
    """
    Vercel internal rewrites set scope['path'] to the rewrite destination ('/api/index.py').
    This middleware restores the original request path from 'x-matched-path' so FastAPI routers match correctly.
    """
    matched_path = request.headers.get("x-matched-path")
    if matched_path and request.scope.get("path") in ("/api/index.py", "/api/index", "/api"):
        request.scope["path"] = matched_path
    return await call_next(request)


@app.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS", "PATCH"])
def fallback_route(full_path: str, request: Request):
    """Fallback handler displaying diagnostic routing info if an endpoint is truly not found."""
    return JSONResponse(
        content={
            "error": "Route not found",
            "requested_path": full_path,
            "scope_path": request.scope.get("path"),
            "x_matched_path": request.headers.get("x-matched-path")
        },
        status_code=404
    )
