"""HTTP client for the Google Apps Script bridge (apps_script/).

The bridge is an Apps Script Web App. Every call is a POST with this JSON body:

    {"secret": "<shared secret>", "operation": "gmail.search", "params": {...}}

and the bridge answers with either

    {"ok": true, "data": {...}}   or   {"ok": false, "error": {"code": "...", "message": "..."}}

Apps Script answers a POST with a 302 redirect to script.googleusercontent.com, so the
client must follow redirects.
"""
import logging
from typing import Any, Protocol

import httpx

from app.config import Settings
from app.errors import BridgeError, BridgeTimeout, ConfigurationError

logger = logging.getLogger(__name__)


class Bridge(Protocol):
    """Anything that can run a bridge operation. Tests use a fake implementation."""

    def call(self, operation: str, params: dict[str, Any] | None = None) -> Any: ...


class AppsScriptBridge:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        self._settings = settings
        self._client = httpx.Client(
            timeout=settings.bridge_timeout_seconds,
            follow_redirects=True,
            transport=transport,
        )

    def call(self, operation: str, params: dict[str, Any] | None = None) -> Any:
        issues = self._settings.bridge_config_issues()
        if issues:
            raise ConfigurationError("Google bridge is not configured: " + " ".join(issues))

        body = {
            "secret": self._settings.apps_script_shared_secret.get_secret_value(),
            "operation": operation,
            "params": params or {},
        }
        # Log the operation name only: parameters can contain email content.
        logger.info("bridge call: %s", operation)
        try:
            response = self._client.post(self._settings.apps_script_url, json=body)
        except httpx.TimeoutException as exc:
            raise BridgeTimeout(
                f"Google did not answer within {self._settings.bridge_timeout_seconds:g} seconds "
                f"({operation}). Please try again."
            ) from exc
        except httpx.HTTPError as exc:
            raise BridgeError(f"Could not reach the Google bridge: {exc.__class__.__name__}.") from exc

        return self._parse(operation, response)

    @staticmethod
    def _parse(operation: str, response: httpx.Response) -> Any:
        if response.status_code >= 400:
            raise BridgeError(f"The Google bridge returned HTTP {response.status_code} for {operation}.")
        try:
            payload = response.json()
        except ValueError as exc:
            # Usually a Google sign-in page: the Web App is not deployed with
            # "Who has access: Anyone", or the URL is not the /exec URL.
            raise BridgeError(
                "The Google bridge did not return JSON. Check that the Web App is deployed "
                "with 'Who has access: Anyone' and that APPS_SCRIPT_URL ends with /exec."
            ) from exc

        if not isinstance(payload, dict) or "ok" not in payload:
            raise BridgeError(f"The Google bridge returned an unexpected response for {operation}.")
        if payload["ok"] is True:
            return payload.get("data")

        error = payload.get("error") or {}
        code = str(error.get("code") or "bridge_error")
        message = str(error.get("message") or "Unknown bridge error.")
        raise BridgeError(message, code=f"bridge_{code}" if not code.startswith("bridge_") else code)

    def close(self) -> None:
        self._client.close()
