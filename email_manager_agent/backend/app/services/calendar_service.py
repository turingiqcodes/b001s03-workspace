"""Google Calendar tools.

Availability and conflicts are always computed from real events fetched through the
bridge; nothing is guessed. Times without a UTC offset are read in USER_TIMEZONE.
"""
from datetime import date, datetime, timedelta
from typing import Any

from pydantic import Field

from app.errors import BridgeError, ValidationFailed
from app.services.availability import (
    BusyInterval,
    WorkingHours,
    find_conflicts,
    find_free_slots,
    working_hours_problems,
)
from app.services.common import (
    EmailAddress,
    Proposal,
    ResourceId,
    ServiceContext,
    ToolInput,
    check_recipient_count,
    localize,
    require_dict,
    require_list,
)

MAX_EVENT_HOURS = 24


# ---------- Inputs ----------

class ListEventsInput(ToolInput):
    start: datetime = Field(description="Range start, ISO 8601. Without an offset it is read in the user's timezone.")
    end: datetime | None = Field(None, description="Range end, ISO 8601. Omit for one day after start.")
    query: str = Field("", max_length=200, description="Optional text to match in event titles/descriptions.")


class GetEventInput(ToolInput):
    event_id: ResourceId = Field(description="Event id from list_events.")


class FindSlotsInput(ToolInput):
    duration_minutes: int = Field(30, ge=5, le=8 * 60, description="Meeting length in minutes (default 30).")
    from_date: date = Field(description="First day to search (YYYY-MM-DD), in the user's timezone.")
    to_date: date | None = Field(None, description="Last day to search, inclusive. Omit to search only from_date.")


class CreateEventInput(ToolInput):
    title: str = Field(min_length=1, max_length=250)
    start: datetime
    end: datetime
    description: str = Field("", max_length=5000)
    location: str = Field("", max_length=500)
    attendees: list[EmailAddress] = Field(default_factory=list, description="Guest email addresses.")
    send_invites: bool = Field(True, description="Email invitations to attendees.")


class UpdateEventInput(ToolInput):
    event_id: ResourceId
    new_start: datetime | None = Field(
        None, description="New start. If new_end is omitted, the original duration is kept."
    )
    new_end: datetime | None = None
    title: str | None = Field(None, min_length=1, max_length=250)
    description: str | None = Field(None, max_length=5000)
    location: str | None = Field(None, max_length=500)
    add_attendees: list[EmailAddress] = Field(default_factory=list)


class CancelEventInput(ToolInput):
    event_id: ResourceId


# ---------- Helpers ----------

def parse_bridge_time(value: Any) -> datetime:
    """Parse an ISO 8601 time from the bridge. Python 3.10 does not accept a trailing 'Z'."""
    if not isinstance(value, str):
        raise BridgeError("The Google bridge returned an event without a valid time.")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        moment = datetime.fromisoformat(text)
    except ValueError as exc:
        raise BridgeError(f"The Google bridge returned an invalid time: {value!r}.") from exc
    if moment.tzinfo is None:
        raise BridgeError("The Google bridge returned a time without a UTC offset.")
    return moment


def _working_hours(ctx: ServiceContext) -> WorkingHours:
    s = ctx.settings
    return WorkingHours(start=s.work_day_start, end=s.work_day_end, days=tuple(s.work_days), tz=s.tz)


def _fetch_events(ctx: ServiceContext, start: datetime, end: datetime, query: str = "") -> list[dict[str, Any]]:
    data = require_dict(
        ctx.bridge.call(
            "calendar.listEvents",
            {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "query": query,
                "max_results": ctx.settings.calendar_list_max_results,
            },
        ),
        "calendar.listEvents",
    )
    return [require_dict(event, "calendar event") for event in require_list(data.get("events", []), "events")]


def busy_intervals(events: list[dict[str, Any]]) -> list[BusyInterval]:
    """Timed events the user has not declined. All-day events do not block time."""
    busy = []
    for event in events:
        if event.get("all_day") or event.get("my_status") == "NO":
            continue
        busy.append(
            BusyInterval(
                start=parse_bridge_time(event.get("start")),
                end=parse_bridge_time(event.get("end")),
                event_id=str(event.get("id", "")),
                title=str(event.get("title", "")),
            )
        )
    return busy


def _check_event_times(ctx: ServiceContext, start: datetime, end: datetime) -> tuple[datetime, datetime]:
    start, end = localize(start, ctx.settings), localize(end, ctx.settings)
    if end <= start:
        raise ValidationFailed("The event must end after it starts.")
    if end - start > timedelta(hours=MAX_EVENT_HOURS):
        raise ValidationFailed(f"Events longer than {MAX_EVENT_HOURS} hours are not supported.")
    if start < ctx.now():
        raise ValidationFailed("The event would start in the past.")
    return start, end


def scheduling_warnings(
    ctx: ServiceContext, start: datetime, end: datetime, ignore_event_id: str | None = None
) -> tuple[list[str], list[dict[str, str]]]:
    """Working-hour problems and real conflicting events for [start, end)."""
    warnings = working_hours_problems(start, end, _working_hours(ctx))
    conflicts = find_conflicts(start, end, busy_intervals(_fetch_events(ctx, start, end)), ignore_event_id)
    conflict_list = [
        {"event_id": c.event_id, "title": c.title, "start": c.start.isoformat(), "end": c.end.isoformat()}
        for c in conflicts
    ]
    if conflict_list:
        titles = ", ".join(f"\"{c['title']}\"" for c in conflict_list)
        warnings.append(f"Conflicts with {len(conflict_list)} existing event(s): {titles}.")
    return warnings, conflict_list


def _get_event(ctx: ServiceContext, event_id: str) -> dict[str, Any]:
    return require_dict(ctx.bridge.call("calendar.getEvent", {"event_id": event_id}), "calendar.getEvent")


def _local_text(moment: datetime, ctx: ServiceContext) -> str:
    return moment.astimezone(ctx.settings.tz).strftime("%a %d %b %Y %H:%M %Z")


# ---------- Read tools ----------

def list_events(ctx: ServiceContext, params: ListEventsInput) -> dict[str, Any]:
    start = localize(params.start, ctx.settings)
    end = localize(params.end, ctx.settings) if params.end else start + timedelta(days=1)
    if end <= start:
        raise ValidationFailed("end must be after start.")
    if end - start > timedelta(days=ctx.settings.availability_max_range_days * 4):
        raise ValidationFailed("The date range is too long; ask for a shorter period.")
    events = _fetch_events(ctx, start, end, params.query)
    return {"timezone": ctx.settings.user_timezone, "count": len(events), "events": events}


def get_event(ctx: ServiceContext, params: GetEventInput) -> dict[str, Any]:
    return _get_event(ctx, params.event_id)


def find_available_slots(ctx: ServiceContext, params: FindSlotsInput) -> dict[str, Any]:
    s = ctx.settings
    to_date = params.to_date or params.from_date
    if to_date < params.from_date:
        raise ValidationFailed("to_date must be on or after from_date.")
    if (to_date - params.from_date).days + 1 > s.availability_max_range_days:
        raise ValidationFailed(f"Search at most {s.availability_max_range_days} days at a time.")

    range_start = datetime.combine(params.from_date, s.work_day_start, tzinfo=s.tz)
    range_end = datetime.combine(to_date, s.work_day_end, tzinfo=s.tz)
    events = _fetch_events(ctx, range_start, range_end)
    slots = find_free_slots(
        busy=busy_intervals(events),
        first_day=params.from_date,
        last_day=to_date,
        duration=timedelta(minutes=params.duration_minutes),
        hours=_working_hours(ctx),
        grid_minutes=s.slot_grid_minutes,
        max_slots=s.max_suggested_slots,
        now=ctx.now(),
    )
    all_day = [e.get("title", "") for e in events if e.get("all_day")]
    return {
        "timezone": s.user_timezone,
        "working_hours": f"{s.work_day_start:%H:%M}-{s.work_day_end:%H:%M}",
        "slot_grid_minutes": s.slot_grid_minutes,
        "slots": [{"start": a.isoformat(), "end": b.isoformat()} for a, b in slots],
        "note": "No free slots in this range." if not slots else "",
        "all_day_events_not_blocking": all_day,
    }


# ---------- Write tools: propose ----------

def propose_create_event(ctx: ServiceContext, params: CreateEventInput) -> Proposal:
    check_recipient_count(ctx.settings, params.attendees)
    start, end = _check_event_times(ctx, params.start, params.end)
    warnings, conflicts = scheduling_warnings(ctx, start, end)
    execute_params = {
        "title": params.title,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "description": params.description,
        "location": params.location,
        "attendees": params.attendees,
        "send_invites": params.send_invites,
    }
    return Proposal(
        summary=f"Create \"{params.title}\" on {_local_text(start, ctx)} ({int((end - start).total_seconds() // 60)} min)",
        details={**execute_params, "conflicts": conflicts, "timezone": ctx.settings.user_timezone},
        execute_params=execute_params,
        warnings=warnings,
    )


def propose_update_event(ctx: ServiceContext, params: UpdateEventInput) -> Proposal:
    check_recipient_count(ctx.settings, params.add_attendees)
    existing = _get_event(ctx, params.event_id)
    if existing.get("recurring"):
        raise ValidationFailed("Changing recurring events is not supported. Edit it in Google Calendar.")
    old_start = parse_bridge_time(existing.get("start"))
    old_end = parse_bridge_time(existing.get("end"))

    execute_params: dict[str, Any] = {"event_id": params.event_id}
    warnings: list[str] = []
    conflicts: list[dict[str, str]] = []
    if params.new_start or params.new_end:
        new_start = localize(params.new_start, ctx.settings) if params.new_start else old_start
        if params.new_end:
            new_end = localize(params.new_end, ctx.settings)
        else:
            new_end = new_start + (old_end - old_start)  # keep the original duration
        new_start, new_end = _check_event_times(ctx, new_start, new_end)
        warnings, conflicts = scheduling_warnings(ctx, new_start, new_end, ignore_event_id=params.event_id)
        execute_params.update(start=new_start.isoformat(), end=new_end.isoformat())
    for field_name in ("title", "description", "location"):
        value = getattr(params, field_name)
        if value is not None:
            execute_params[field_name] = value
    if params.add_attendees:
        execute_params["add_attendees"] = params.add_attendees
    if len(execute_params) == 1:
        raise ValidationFailed("Nothing to change for this event.")

    if "start" in execute_params:
        summary = (
            f"Move \"{existing.get('title', '')}\" from {_local_text(old_start, ctx)} "
            f"to {_local_text(parse_bridge_time(execute_params['start']), ctx)}"
        )
    else:
        summary = f"Update \"{existing.get('title', '')}\""
    return Proposal(
        summary=summary,
        details={"current": existing, "changes": execute_params, "conflicts": conflicts},
        execute_params=execute_params,
        warnings=warnings,
    )


def propose_cancel_event(ctx: ServiceContext, params: CancelEventInput) -> Proposal:
    existing = _get_event(ctx, params.event_id)
    if existing.get("recurring"):
        raise ValidationFailed("Cancelling recurring events is not supported. Edit it in Google Calendar.")
    start = parse_bridge_time(existing.get("start"))
    return Proposal(
        summary=f"Cancel \"{existing.get('title', '')}\" on {_local_text(start, ctx)}",
        details={"event": existing},
        execute_params={"event_id": params.event_id},
        warnings=[
            "Cancelling removes this event from your calendar."
            + (" It has guests, who may be notified by Google Calendar." if existing.get("attendees") else "")
        ],
    )


# ---------- Write tools: execute ----------

def _recheck_conflicts(ctx: ServiceContext, p: dict[str, Any], acknowledged: list[str], ignore: str | None) -> None:
    """Calendars change while a proposal waits. Refuse if a new, unapproved conflict has appeared."""
    if "start" not in p:
        return
    _, conflicts = scheduling_warnings(ctx, parse_bridge_time(p["start"]), parse_bridge_time(p["end"]), ignore)
    new = [c for c in conflicts if c["event_id"] not in acknowledged]
    if new:
        titles = ", ".join(f"\"{c['title']}\"" for c in new)
        raise ValidationFailed(
            f"A new conflicting event appeared since this was proposed ({titles}). Nothing was changed; "
            "please ask again so you can review the new conflict."
        )


def execute_create_event(ctx: ServiceContext, p: dict[str, Any], acknowledged: list[str]) -> Any:
    _recheck_conflicts(ctx, p, acknowledged, None)
    return ctx.bridge.call("calendar.createEvent", p)


def execute_update_event(ctx: ServiceContext, p: dict[str, Any], acknowledged: list[str]) -> Any:
    _recheck_conflicts(ctx, p, acknowledged, p["event_id"])
    return ctx.bridge.call("calendar.updateEvent", p)


def execute_cancel_event(ctx: ServiceContext, p: dict[str, Any], acknowledged: list[str]) -> Any:
    return ctx.bridge.call("calendar.cancelEvent", p)

