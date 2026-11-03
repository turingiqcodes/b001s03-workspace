"""Start the backend with the host and port from .env (BACKEND_HOST / BACKEND_PORT).

    cd backend && ../.venv/bin/python run.py

Refuses to listen on anything but this computer: the app has no login of its own.
"""
import ipaddress
import sys

import uvicorn

from app.config import get_settings


def main() -> None:
    settings = get_settings()
    host = settings.backend_host
    is_local = host == "localhost"
    if not is_local:
        try:
            is_local = ipaddress.ip_address(host).is_loopback
        except ValueError:
            is_local = False
    if not is_local:
        sys.exit(f"BACKEND_HOST={host} is not a loopback address. Use 127.0.0.1: this app is local-only.")
    uvicorn.run("app.main:app", host=host, port=settings.backend_port)


if __name__ == "__main__":
    main()
