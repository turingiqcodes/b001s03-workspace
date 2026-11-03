"""Shared building blocks for the Gmail, Calendar and Drive services."""
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated, Any, Callable

from pydantic import AfterValidator, BaseModel, ConfigDict

from app.config import Settings
from app.errors import BridgeError, ValidationFailed
from app.services.bridge import Bridge

# Deliberately simple: one "@", no spaces, a dot in the domain. Gmail does the real check.
_EMAIL_PATTERN = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")
# Gmail message ids, Calendar iCal ids and Drive ids only use these characters.
_ID_PATTERN = re.compile(r"^[A-Za-z0-9_\-@.]{1,256}$")


def _check_email(value: str) -> str:
    value = value.strip()
    if not _EMAIL_PATTERN.match(value):
        raise ValueError(f"'{value}' is not a valid email address")
    return value


def _check_id(value: str) -> str:
    value = value.strip()
    if not _ID_PATTERN.match(value):
        raise ValueError("id contains unsupported characters")
    return value


EmailAddress = Annotated[str, AfterValidator(_check_email)]
ResourceId = Annotated[str, AfterValidator(_check_id)]


class ToolInput(BaseModel):
    """Base for every tool's input. Unknown fields are rejected, not ignored."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


@dataclass
class ServiceContext:
    settings: Settings
    bridge: Bridge
    now: Callable[[], datetime]


@dataclass
class Proposal:
    """A validated write operation, waiting for the user's approval.

    `execute_params` is what will actually run; it is stored on the server and never
    taken from the frontend.
    """

    summary: str
    details: dict[str, Any]
    execute_params: dict[str, Any]
    warnings: list[str] = field(default_factory=list)


def localize(moment: datetime, settings: Settings) -> datetime:
    """Give naive datetimes the user's timezone; leave aware ones alone."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=settings.tz)
    return moment


def require_dict(value: Any, what: str) -> dict[str, Any]:
    """Check that a bridge result has the expected shape before using it."""
    if not isinstance(value, dict):
        raise BridgeError(f"The Google bridge returned malformed data for {what}.")
    return value


def require_list(value: Any, what: str) -> list[Any]:
    if not isinstance(value, list):
        raise BridgeError(f"The Google bridge returned malformed data for {what}.")
    return value


def check_recipient_count(settings: Settings, *recipient_lists: list[str]) -> None:
    total = sum(len(recipients) for recipients in recipient_lists)
    if total > settings.max_recipients:
        raise ValidationFailed(f"Too many recipients ({total}); the limit is {settings.max_recipients}.")


def check_text_limit(text: str, limit: int, what: str) -> None:
    if len(text) > limit:
        raise ValidationFailed(f"The {what} is {len(text)} characters; the limit is {limit}.")
