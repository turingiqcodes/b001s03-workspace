"""FastAPI application: middleware, error handlers and startup.

Run from the backend/ folder:  ../.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
"""
import ipaddress
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.routes import router
from app.config import get_settings
from app.dependencies import Container
from app.errors import AppError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def _is_loopback(host: str | None) -> bool:
    if not host:
        return False
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def create_app(container: Container | None = None) -> FastAPI:
    container = container or Container.build(get_settings())
    settings = container.settings

    app = FastAPI(title="Email Manager Agent", version="1.0.0")
    app.state.container = container

    # Only answer requests addressed to localhost (blocks DNS-rebinding style tricks)...
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
    # ...and let only the local frontend call the API from a browser.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin],
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @app.middleware("http")
    async def loopback_only(request: Request, call_next):
        client_host = request.client.host if request.client else None
        if not _is_loopback(client_host):
            return _error(403, "forbidden", "This app only accepts requests from this computer.")
        return await call_next(request)

    @app.exception_handler(AppError)
    async def handle_app_error(_request: Request, exc: AppError) -> JSONResponse:
        return _error(exc.http_status, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        problems = []
        for err in exc.errors():
            location = ".".join(str(p) for p in err.get("loc", ()) if p != "body") or "body"
            problems.append(f"{location}: {err.get('msg', 'invalid')}")
        return _error(422, "validation_error", "Invalid request. " + "; ".join(problems))

    @app.exception_handler(Exception)
    async def handle_unexpected(_request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unexpected error")
        return _error(500, "internal_error", "Something went wrong on the server. Check the backend log.")

    app.include_router(router)
    return app


app = create_app()
