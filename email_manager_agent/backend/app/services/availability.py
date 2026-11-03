"""Pure scheduling helpers: free-slot search, conflict detection and working-hour checks.

Nothing here talks to Google. The calendar service fetches real events through the
bridge and passes them in, so these functions can be tested with plain data.
"""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class BusyInterval:
    start: datetime
    end: datetime
    event_id: str = ""
    title: str = ""


@dataclass(frozen=True)
class WorkingHours:
    start: time
    end: time
    days: tuple[int, ...]  # Monday=0 ... Sunday=6
    tz: ZoneInfo


def overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    """True when two half-open intervals [start, end) share any time."""
    return a_start < b_end and b_start < a_end


def find_conflicts(
    start: datetime, end: datetime, busy: list[BusyInterval], ignore_event_id: str | None = None
) -> list[BusyInterval]:
    """Events in `busy` that overlap [start, end), optionally ignoring one event (the one being moved)."""
    return [
        interval
        for interval in busy
        if interval.event_id != ignore_event_id and overlaps(start, end, interval.start, interval.end)
    ]


def working_hours_problems(start: datetime, end: datetime, hours: WorkingHours) -> list[str]:
    """Readable reasons why [start, end) falls outside working hours. Empty list means it fits."""
    local_start = start.astimezone(hours.tz)
    local_end = end.astimezone(hours.tz)
    problems = []
    if local_start.weekday() not in hours.days:
        problems.append(f"{local_start:%A} is not a working day.")
    if local_start.date() != local_end.date() and local_end.time() != time(0, 0):
        problems.append("The event spans more than one day.")
    if local_start.time() < hours.start or local_end.time() > hours.end or local_end.time() == time(0, 0):
        problems.append(
            f"The event is outside working hours ({hours.start:%H:%M}-{hours.end:%H:%M} {hours.tz.key})."
        )
    return problems


def _round_up_to_grid(moment: datetime, grid_minutes: int) -> datetime:
    """Round up to the next wall-clock multiple of grid_minutes (e.g. 10:07 -> 10:30 on a 30 min grid)."""
    midnight = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    minutes_since_midnight = (moment - midnight).total_seconds() / 60
    steps = -(-minutes_since_midnight // grid_minutes)  # ceiling division
    return midnight + timedelta(minutes=steps * grid_minutes)


def find_free_slots(
    *,
    busy: list[BusyInterval],
    first_day: date,
    last_day: date,
    duration: timedelta,
    hours: WorkingHours,
    grid_minutes: int,
    max_slots: int,
    now: datetime,
) -> list[tuple[datetime, datetime]]:
    """Free [start, end) slots of `duration`, inside working hours, on the time grid, after `now`.

    Slots start on wall-clock multiples of `grid_minutes`, never overlap a busy interval,
    and at most `max_slots` are returned (earliest first).
    """
    slots: list[tuple[datetime, datetime]] = []
    step = timedelta(minutes=grid_minutes)
    day = first_day
    while day <= last_day and len(slots) < max_slots:
        if day.weekday() in hours.days:
            window_start = datetime.combine(day, hours.start, tzinfo=hours.tz)
            window_end = datetime.combine(day, hours.end, tzinfo=hours.tz)
            candidate = _round_up_to_grid(max(window_start, now.astimezone(hours.tz)), grid_minutes)
            while candidate + duration <= window_end and len(slots) < max_slots:
                candidate_end = candidate + duration
                if not find_conflicts(candidate, candidate_end, busy):
                    slots.append((candidate, candidate_end))
                candidate += step
        day += timedelta(days=1)
    return slots
