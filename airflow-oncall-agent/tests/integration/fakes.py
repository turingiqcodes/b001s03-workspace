"""A tiny programmable HTTP server for httpx.MockTransport.

Routes are matched on (method, path). A route's handler is either a static response
(status, json) or a list of them (served in order, the last one repeating), or a
callable receiving the request. Every request is recorded for assertions.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Union

import httpx

Reply = Union[tuple[int, Any], httpx.Response, Callable[[httpx.Request], httpx.Response]]


class FakeServer:
    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], list[Reply]] = {}
        self.requests: list[httpx.Request] = []

    def on(self, method: str, path: str, *replies: Reply) -> "FakeServer":
        self.routes[(method.upper(), path)] = list(replies)
        return self

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        key = (request.method, request.url.path)
        if key not in self.routes:
            return httpx.Response(404, json={"detail": f"no fake route for {key}"})
        replies = self.routes[key]
        reply = replies.pop(0) if len(replies) > 1 else replies[0]
        if callable(reply) and not isinstance(reply, httpx.Response):
            return reply(request)
        if isinstance(reply, httpx.Response):
            return reply
        status, body = reply
        if isinstance(body, (dict, list)):
            return httpx.Response(status, json=body)
        return httpx.Response(status, text=str(body))

    def calls(self, method: str, path: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == method.upper() and r.url.path == path]

    @staticmethod
    def body(request: httpx.Request) -> Any:
        return json.loads(request.content)


def raise_timeout(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("timed out", request=request)


def raise_connect(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("refused", request=request)
