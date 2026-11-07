"""Fault injection shared by the mock clients (outages, malformed data, timeouts)."""
from __future__ import annotations

import threading
from typing import Any

from ...exceptions import IntegrationError


class FaultInjector:
    """Mixin: ``unavailable=True`` makes every call fail like an outage;
    ``fail_next("method", exc)`` makes the next call(s) of one method raise ``exc``."""

    error_cls: type[IntegrationError] = IntegrationError

    def __init__(self) -> None:
        self.unavailable = False
        self._failures: dict[str, list[Exception]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._fault_lock = threading.Lock()

    def fail_next(self, method: str, exc: Exception, times: int = 1) -> None:
        with self._fault_lock:
            self._failures.setdefault(method, []).extend([exc] * times)

    def _enter(self, method: str, **args: Any) -> None:
        with self._fault_lock:
            self.calls.append((method, args))
            if self.unavailable:
                raise self.error_cls(f"{method}: service unavailable (mock)", retriable=True, status_code=503)
            queue = self._failures.get(method)
            if queue:
                raise queue.pop(0)

    def call_count(self, method: str) -> int:
        return sum(1 for name, _ in self.calls if name == method)
