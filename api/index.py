import sys
import os
import json

def app(environ, start_response):
    status = '200 OK'
    headers = [('Content-type', 'application/json; charset=utf-8')]
    start_response(status, headers)

    # Test what packages can be imported
    installed = {}
    for pkg in ["fastapi", "pydantic", "sqlalchemy", "pg8000"]:
        try:
            m = __import__(pkg)
            installed[pkg] = getattr(m, "__version__", "installed")
        except Exception as e:
            installed[pkg] = f"ERROR: {type(e).__name__}: {e}"

    data = {
        "status": "healthy",
        "service": "zkteco-odoo-relay",
        "python_version": sys.version,
        "path_info": environ.get("PATH_INFO"),
        "installed_packages": installed
    }
    return [json.dumps(data, indent=2).encode("utf-8")]
