"""Calendar service: listing, availability, creation, updates, cancellation, conflicts and timezones."""
from datetime import date, datetime

import pytest

from app.errors import BridgeError, ValidationFailed
from app.services import calendar_service as cal

from tests.conftest import make_settings
from app.services.common import ServiceContext


def event(eid, start, end, title="Meeting", **extra):
    return {"id": eid, "title": title, "start": start, "end": end, "all_day": False, "my_status": "YES", **extra}


EXISTING = event("e1", "2026-11-10T10:00:00+00:00", "2026-11-10T11:00:00+00:00", "Design review")


def test_list_events_reads_naive_times_in_user_timezone(ctx, bridge):
    bridge.on("calendar.listEvents", {"events": [EXISTING]})
    out = cal.list_events(ctx, cal.ListEventsInput(start="2026-11-10T00:00:00", end="2026-11-11T00:00:00"))
    assert out["count"] == 1 and out["timezone"] == "UTC"
    assert bridge.calls[0][1]["start"] == "2026-11-10T00:00:00+00:00"


def test_list_events_rejects_bad_range(ctx):
    with pytest.raises(ValidationFailed):
        cal.list_events(ctx, cal.ListEventsInput(start="2026-11-11T00:00:00", end="2026-11-10T00:00:00"))


def test_find_slots_uses_real_events(ctx, bridge):
    bridge.on("calendar.listEvents", {"events": [
        event("e1", "2026-11-10T09:00:00Z", "2026-11-10T12:00:00Z"),
        event("e2", "2026-11-10T12:00:00Z", "2026-11-10T13:00:00Z", my_status="NO"),  # declined: not busy
        {"id": "e3", "title": "Holiday", "start": "2026-11-10T00:00:00Z", "end": "2026-11-11T00:00:00Z", "all_day": True},
    ]})
    out = cal.find_available_slots(ctx, cal.FindSlotsInput(duration_minutes=60, from_date=date(2026, 11, 10), to_date=date(2026, 11, 10)))
    assert out["slots"][0] == {"start": "2026-11-10T12:00:00+00:00", "end": "2026-11-10T13:00:00+00:00"}
    assert len(out["slots"]) == 5  # MAX_SUGGESTED_SLOTS
    assert out["all_day_events_not_blocking"] == ["Holiday"]


def test_find_slots_range_limit(ctx, settings):
    settings.availability_max_range_days = 3
    with pytest.raises(ValidationFailed, match="at most 3 days"):
        cal.find_available_slots(ctx, cal.FindSlotsInput(duration_minutes=30, from_date=date(2026, 11, 9), to_date=date(2026, 11, 12)))


def test_find_slots_reports_none(ctx, bridge):
    bridge.on("calendar.listEvents", {"events": [event("e1", "2026-11-10T08:00:00Z", "2026-11-10T18:00:00Z")]})
    out = cal.find_available_slots(ctx, cal.FindSlotsInput(duration_minutes=30, from_date=date(2026, 11, 10), to_date=date(2026, 11, 10)))
    assert out["slots"] == [] and "No free slots" in out["note"]


def test_create_event_without_conflict(ctx, bridge):
    bridge.on("calendar.listEvents", {"events": []})
    proposal = cal.propose_create_event(ctx, cal.CreateEventInput(title="1:1", start="2026-11-10T14:00:00", end="2026-11-10T14:30:00", attendees=["bob@example.com"]))
    assert proposal.warnings == []
    assert proposal.execute_params["start"] == "2026-11-10T14:00:00+00:00"
    assert "30 min" in proposal.summary


def test_create_event_conflict_is_flagged_not_hidden(ctx, bridge):
    bridge.on("calendar.listEvents", {"events": [EXISTING]})
    proposal = cal.propose_create_event(ctx, cal.CreateEventInput(title="Clash", start="2026-11-10T10:30:00", end="2026-11-10T11:30:00"))
    assert any("Design review" in w for w in proposal.warnings)
    assert proposal.details["conflicts"][0]["event_id"] == "e1"


def test_create_event_outside_working_hours_warns(ctx, bridge):
    bridge.on("calendar.listEvents", {"events": []})
    proposal = cal.propose_create_event(ctx, cal.CreateEventInput(title="Late", start="2026-11-10T19:00:00", end="2026-11-10T20:00:00"))
    assert any("outside working hours" in w for w in proposal.warnings)


@pytest.mark.parametrize("start,end,message", [
    ("2026-11-10T10:00:00", "2026-11-10T09:00:00", "end after it starts"),
    ("2026-11-01T10:00:00", "2026-11-01T11:00:00", "in the past"),
    ("2026-11-10T10:00:00", "2026-11-12T10:00:00", "longer than"),
])
def test_create_event_time_validation(ctx, start, end, message):
    with pytest.raises(ValidationFailed, match=message):
        cal.propose_create_event(ctx, cal.CreateEventInput(title="x", start=start, end=end))


def test_move_event_preserves_duration(ctx, bridge):
    bridge.on("calendar.getEvent", EXISTING)
    bridge.on("calendar.listEvents", {"events": [EXISTING]})  # itself: ignored as a conflict
    proposal = cal.propose_update_event(ctx, cal.UpdateEventInput(event_id="e1", new_start="2026-11-11T15:00:00"))
    assert proposal.execute_params == {"event_id": "e1", "start": "2026-11-11T15:00:00+00:00", "end": "2026-11-11T16:00:00+00:00"}
    assert proposal.warnings == []
    assert proposal.summary.startswith('Move "Design review"')


def test_move_event_conflict(ctx, bridge):
    other = event("e2", "2026-11-11T15:30:00Z", "2026-11-11T16:30:00Z", "Standup")
    bridge.on("calendar.getEvent", EXISTING)
    bridge.on("calendar.listEvents", {"events": [other]})
    proposal = cal.propose_update_event(ctx, cal.UpdateEventInput(event_id="e1", new_start="2026-11-11T15:00:00"))
    assert proposal.details["conflicts"][0]["event_id"] == "e2"


def test_update_requires_a_change_and_rejects_recurring(ctx, bridge):
    bridge.on("calendar.getEvent", EXISTING)
    with pytest.raises(ValidationFailed, match="Nothing to change"):
        cal.propose_update_event(ctx, cal.UpdateEventInput(event_id="e1"))
    bridge.on("calendar.getEvent", {**EXISTING, "recurring": True})
    with pytest.raises(ValidationFailed, match="recurring"):
        cal.propose_update_event(ctx, cal.UpdateEventInput(event_id="e1", title="New"))
    with pytest.raises(ValidationFailed, match="recurring"):
        cal.propose_cancel_event(ctx, cal.CancelEventInput(event_id="e1"))


def test_cancel_event_proposal(ctx, bridge):
    bridge.on("calendar.getEvent", {**EXISTING, "attendees": ["bob@example.com"]})
    proposal = cal.propose_cancel_event(ctx, cal.CancelEventInput(event_id="e1"))
    assert proposal.summary.startswith('Cancel "Design review"')
    assert "removes this event" in proposal.warnings[0] and "guests" in proposal.warnings[0]


def test_execute_refuses_new_unacknowledged_conflict(ctx, bridge):
    late_arrival = event("e9", "2026-11-10T14:00:00Z", "2026-11-10T15:00:00Z", "New thing")
    bridge.on("calendar.listEvents", {"events": [late_arrival]})
    bridge.on("calendar.createEvent", {"event_id": "new"})
    params = {"title": "1:1", "start": "2026-11-10T14:00:00+00:00", "end": "2026-11-10T14:30:00+00:00"}
    with pytest.raises(ValidationFailed, match="new conflicting event"):
        cal.execute_create_event(ctx, params, acknowledged=[])
    assert "calendar.createEvent" not in bridge.ops()
    assert cal.execute_create_event(ctx, params, acknowledged=["e9"]) == {"event_id": "new"}


def test_timezone_handling(bridge, now_fn):
    ctx = ServiceContext(settings=make_settings(user_timezone="America/New_York"), bridge=bridge, now=now_fn)
    bridge.on("calendar.listEvents", {"events": []})
    proposal = cal.propose_create_event(ctx, cal.CreateEventInput(title="x", start="2026-11-10T10:00:00", end="2026-11-10T11:00:00"))
    assert proposal.execute_params["start"] == "2026-11-10T10:00:00-05:00"
    assert proposal.warnings == []  # 10:00 New York time is inside working hours
    # An explicit offset is kept as given (15:00 UTC = 10:00 New York).
    proposal = cal.propose_create_event(ctx, cal.CreateEventInput(title="x", start="2026-11-10T15:00:00Z", end="2026-11-10T16:00:00Z"))
    assert proposal.warnings == []


def test_bridge_time_parsing():
    assert cal.parse_bridge_time("2026-11-10T10:00:00Z") == datetime.fromisoformat("2026-11-10T10:00:00+00:00")
    with pytest.raises(BridgeError):
        cal.parse_bridge_time("2026-11-10T10:00:00")  # no offset
    with pytest.raises(BridgeError):
        cal.parse_bridge_time(None)


def test_find_slots_defaults_to_one_day_and_30_minutes(ctx, bridge):
    """Small models often send only the start date; that means a single day."""
    bridge.on("calendar.listEvents", {"events": []})
    out = cal.find_available_slots(ctx, cal.FindSlotsInput(from_date=date(2026, 11, 10)))
    assert all(s["start"].startswith("2026-11-10") for s in out["slots"])
    assert out["slots"][0] == {"start": "2026-11-10T09:00:00+00:00", "end": "2026-11-10T09:30:00+00:00"}
    assert bridge.calls[0][1]["end"] == "2026-11-10T17:00:00+00:00"


def test_list_events_end_defaults_to_one_day(ctx, bridge):
    bridge.on("calendar.listEvents", {"events": []})
    cal.list_events(ctx, cal.ListEventsInput(start="2026-11-10T00:00:00"))
    assert bridge.calls[0][1]["end"] == "2026-11-11T00:00:00+00:00"
