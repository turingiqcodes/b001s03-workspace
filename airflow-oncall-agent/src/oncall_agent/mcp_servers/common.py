"""Shared plumbing for the MCP servers (SRS Part 8).

* :class:`Runtime` builds the same clients and services the agent uses (NFR-017): one
  Airflow/Linear client, the store, the audit trail, the policy engine, recovery and the
  Linear incident service. No API logic is duplicated here.
* :func:`tool_call` wraps every tool: argument validation errors, permission refusals
  and integration failures become a structured ``{"ok": false, "error": {...}}`` result
  (never a stack trace), secrets are redacted from every result, and each call is
  audited as ``mcp_tool_call`` (§8.3 "log sensitive operations").
* An MCP tool call is never authorization: write tools are gated by configuration and
  still pass through the policy engine / approval rules.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..config import Settings
from ..exceptions import (
    IntegrationError,
    OncallError,
    SafetyError,
    StoreError,
    WriteDisabledError,
)
from ..factory import Integrations, build_audit, build_integrations, build_store
from ..logging_config import redact_value
from ..models import AuditEventType
from ..ownership import OwnershipResolver
from ..policy import PolicyEngine
from ..services.audit import AuditService
from ..services.incident_manager import LinearIncidentService
from ..services.recovery import RecoveryService
from ..store import Store

log = logging.getLogger(__name__)

DAG_ID = re.compile(r"^[A-Za-z0-9_.\-]{1,250}$")
TASK_ID = DAG_ID
RUN_ID = re.compile(r"^[A-Za-z0-9_.~:+\-]{1,250}$")
ISSUE_ID = re.compile(r"^[A-Za-z0-9\-]{1,64}$")
LABEL = re.compile(r"^[\w:.\- ]{1,60}$")


class InvalidArgument(OncallError):
    """A tool argument failed validation."""


class NotFound(OncallError):
    """The referenced object does not exist."""


@dataclass
class Runtime:
    settings: Settings
    store: Store
    audit: AuditService
    integrations: Integrations
    policy: PolicyEngine
    recovery: RecoveryService
    linear_sync: LinearIncidentService

    @classmethod
    def build(cls, settings: Settings, *, integrations: Integrations | None = None, store: Store | None = None) -> "Runtime":
        store = store or build_store(settings)
        audit = build_audit(settings, store)
        integrations = integrations or build_integrations(settings)
        policy = PolicyEngine(settings.policy)
        recovery = RecoveryService(integrations.airflow, store, audit, policy, settings.policy)
        ownership = OwnershipResolver(integrations.linear, settings.ownership.user_map, settings.linear.default_assignee)
        linear_sync = LinearIncidentService(integrations.linear, store, audit, settings, ownership)
        return cls(settings, store, audit, integrations, policy, recovery, linear_sync)

    def close(self) -> None:
        self.integrations.close()
        self.store.close()


# --------------------------------------------------------------------------- validation


def ident(name: str, value: Any, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise InvalidArgument(f"{name} is not valid")
    return value


def bounded_int(name: str, value: Any, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise InvalidArgument(f"{name} must be an integer between {low} and {high}")
    return value


def bounded_str(name: str, value: Any, max_len: int, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > max_len or (not allow_empty and not value.strip()):
        raise InvalidArgument(f"{name} must be a {'' if allow_empty else 'non-empty '}string of at most {max_len} characters")
    if any(ord(ch) < 32 and ch not in "\n\t\r" for ch in value):
        raise InvalidArgument(f"{name} contains control characters")
    return value


# --------------------------------------------------------------------------- results


def _error(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message}}


def tool_call(rt: Runtime, server: str, tool: str, args: dict[str, Any], fn: Callable[[], Any], *, write: bool = False) -> dict[str, Any]:
    """Run a tool body and convert the outcome into a structured, redacted result."""
    try:
        result: dict[str, Any] = {"ok": True, "data": fn()}
    except InvalidArgument as exc:
        result = _error("invalid_argument", str(exc))
    except NotFound as exc:
        result = _error("not_found", str(exc))
    except WriteDisabledError as exc:
        result = _error("write_disabled", str(exc))
    except SafetyError as exc:
        result = _error("not_permitted", str(exc))
    except IntegrationError as exc:
        result = _error("upstream_error", f"{exc.service}: {exc}")
    except StoreError as exc:
        result = _error("internal_error", f"state store: {exc}")
    except Exception:  # never leak a traceback to the client
        log.exception("MCP tool %s.%s crashed", server, tool)
        result = _error("internal_error", "unexpected error; see the agent log")
    result = redact_value(result)
    if write or not result["ok"]:
        # Link the audit row to the incident only if it exists, so a call naming a bogus
        # incident is still recorded (not rejected by the foreign key).
        incident_id = args.get("incident_id")
        try:
            linked = incident_id if isinstance(incident_id, int) and rt.store.get_incident(incident_id) is not None else None
        except StoreError:
            linked = None
        rt.audit.record(
            AuditEventType.MCP_TOOL_CALL,
            actor=f"mcp:{server}",
            action=tool,
            incident_id=linked,
            input_summary=redact_value(args),
            output_summary=result["error"] if not result["ok"] else "ok",
            success=result["ok"],
            details={"write": write},
        )
    return result
