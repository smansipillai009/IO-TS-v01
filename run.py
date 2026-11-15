"""Entrypoint script that reads PORT from environment as integer directly in Python."""
import os
import uvicorn
from app.main import app

if __name__ == "__main__":
    port_str = os.environ.get("PORT", "8000")
    try:
        port = int(port_str)
    except (ValueError, TypeError):
        port = 8000
    print(f"[RUNNER] Starting Uvicorn server on 0.0.0.0:{port} ...", flush=True)
    uvicorn.run(app, host="0.0.0.0", port=port)
