"""Apps Script bridge client: request format, redirects, timeouts and error mapping."""
import json

import httpx
import pytest

from app.errors import BridgeError, BridgeTimeout, ConfigurationError
from app.services.bridge import AppsScriptBridge

from tests.conftest import make_settings


def bridge_with(handler, **settings):
    return AppsScriptBridge(make_settings(**settings), transport=httpx.MockTransport(handler))


def test_sends_secret_operation_and_params_and_follows_redirect():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "script.google.com":
            seen["body"] = json.loads(request.content)
            return httpx.Response(302, headers={"Location": "https://script.googleusercontent.com/echo?x=1"})
        return httpx.Response(200, json={"ok": True, "data": {"messages": []}})

    assert bridge_with(handler).call("gmail.search", {"query": "x"}) == {"messages": []}
    assert seen["body"] == {"secret": "test-secret", "operation": "gmail.search", "params": {"query": "x"}}


def test_error_payload_becomes_bridge_error():
    handler = lambda r: httpx.Response(200, json={"ok": False, "error": {"code": "not_found", "message": "No such message"}})
    with pytest.raises(BridgeError) as info:
        bridge_with(handler).call("gmail.read", {"message_id": "x"})
    assert info.value.code == "bridge_not_found" and info.value.message == "No such message"


def test_timeout_becomes_bridge_timeout():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(BridgeTimeout, match="30 seconds"):
        bridge_with(handler).call("gmail.search", {})


def test_non_json_response_explains_deployment_problem():
    handler = lambda r: httpx.Response(200, text="<html>Sign in</html>")
    with pytest.raises(BridgeError, match="Who has access: Anyone"):
        bridge_with(handler).call("ping")


@pytest.mark.parametrize("payload", [[1, 2], {"data": 1}, "ok"])
def test_unexpected_shape(payload):
    with pytest.raises(BridgeError, match="unexpected"):
        bridge_with(lambda r: httpx.Response(200, json=payload)).call("ping")


def test_http_error_status():
    with pytest.raises(BridgeError, match="HTTP 500"):
        bridge_with(lambda r: httpx.Response(500)).call("ping")


def test_connection_error():
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(BridgeError, match="Could not reach"):
        bridge_with(handler).call("ping")


def test_missing_configuration_does_not_send():
    calls = []
    bridge = bridge_with(lambda r: calls.append(r) or httpx.Response(200), apps_script_url=None)
    with pytest.raises(ConfigurationError, match="APPS_SCRIPT_URL"):
        bridge.call("ping")
    assert calls == []


def test_rejects_non_google_url():
    bridge = bridge_with(lambda r: httpx.Response(200), apps_script_url="https://evil.example.com/exec")
    with pytest.raises(ConfigurationError, match="script.google.com"):
        bridge.call("ping")
