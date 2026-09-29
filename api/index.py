import os
import sys
import traceback

# Ensure root directory is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_cached_app = None
_startup_error = None

try:
    from app.main import app as _fastapi_app
    _cached_app = _fastapi_app
except BaseException as _exc:
    _startup_error = traceback.format_exc()
    print(f"STARTUP_CRASH: {_startup_error}", file=sys.stderr)


async def app(scope, receive, send):
    """Universal ASGI handler that executes FastAPI or surfaces exact crash traceback."""
    global _cached_app, _startup_error

    # If FastAPI loaded cleanly, delegate all traffic directly to it
    if _cached_app is not None:
        return await _cached_app(scope, receive, send)

    # If startup failed, format and return the exact exception traceback in the HTTP response
    error_body = (
        f"--- Vercel Python Runtime Cold-Start Failure ---\n\n"
        f"{_startup_error}\n"
    ).encode("utf-8")

    await send({
        "type": "http.response.start",
        "status": 500,
        "headers": [
            [b"content-type", b"text/plain; charset=utf-8"],
            [b"content-length", str(len(error_body)).encode("ascii")],
        ],
    })
    await send({
        "type": "http.response.body",
        "body": error_body,
    })
