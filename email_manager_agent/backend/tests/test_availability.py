"""Pure scheduling logic: free slots, grid, working hours, conflicts and timezones."""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app.services.availability import (
    BusyInterval, WorkingHours, find_conflicts, find_free_slots, overlaps, working_hours_problems,
)

UTC = ZoneInfo("UTC")
HOURS = WorkingHours(start=time(9), end=time(17), days=(0, 1, 2, 3, 4), tz=UTC)
MONDAY = date(2026, 11, 9)


def at(hour, minute=0, day=MONDAY, tz=UTC):
    return datetime.combine(day, time(hour, minute), tzinfo=tz)


def slots(busy=(), duration=30, now=None, days=0, grid=30, max_slots=5, hours=HOURS):
    return find_free_slots(
        busy=list(busy), first_day=MONDAY, last_day=MONDAY + timedelta(days=days),
        duration=timedelta(minutes=duration), hours=hours, grid_minutes=grid,
        max_slots=max_slots, now=now or at(0),
    )


def test_overlap_is_half_open():
    assert overlaps(at(9), at(10), at(9, 30), at(11))
    assert not overlaps(at(9), at(10), at(10), at(11))  # back-to-back is fine


def test_slots_respect_grid_and_max():
    result = slots(max_slots=3)
    assert result == [(at(9), at(9, 30)), (at(9, 30), at(10)), (at(10), at(10, 30))]


def test_slots_skip_busy_time():
    result = slots(busy=[BusyInterval(at(9), at(11))], max_slots=2)
    assert result[0] == (at(11), at(11, 30))


def test_slot_must_fit_before_end_of_day():
    result = slots(duration=60, busy=[BusyInterval(at(9), at(16, 30))])
    assert result == []  # 16:30-17:30 would run past 17:00


def test_slots_start_after_now_on_grid():
    result = slots(now=at(10, 7), max_slots=1)
    assert result == [(at(10, 30), at(11))]


def test_weekends_are_skipped():
    saturday_start = find_free_slots(
        busy=[], first_day=date(2026, 11, 14), last_day=date(2026, 11, 16), duration=timedelta(minutes=30),
        hours=HOURS, grid_minutes=30, max_slots=1, now=at(0),
    )
    assert saturday_start[0][0].date() == date(2026, 11, 16)  # Monday


def test_slots_in_other_timezone():
    kolkata = ZoneInfo("Asia/Kolkata")
    hours = WorkingHours(start=time(9), end=time(17), days=(0, 1, 2, 3, 4), tz=kolkata)
    result = slots(hours=hours, max_slots=1, now=datetime(2026, 11, 9, 0, 0, tzinfo=timezone.utc))
    start = result[0][0]
    assert start.utcoffset() == timedelta(hours=5, minutes=30)
    assert start.astimezone(timezone.utc).hour == 3 and start.astimezone(timezone.utc).minute == 30


def test_find_conflicts_ignores_the_event_being_moved():
    busy = [BusyInterval(at(10), at(11), event_id="e1"), BusyInterval(at(10, 30), at(12), event_id="e2")]
    assert [c.event_id for c in find_conflicts(at(10), at(11), busy)] == ["e1", "e2"]
    assert [c.event_id for c in find_conflicts(at(10), at(11), busy, ignore_event_id="e1")] == ["e2"]


def test_working_hours_problems():
    assert working_hours_problems(at(9), at(10), HOURS) == []
    assert "outside working hours" in working_hours_problems(at(8), at(9), HOURS)[0]
    assert "not a working day" in working_hours_problems(at(10, day=date(2026, 11, 15)), at(11, day=date(2026, 11, 15)), HOURS)[0]
    assert working_hours_problems(at(16), at(17), HOURS) == []
