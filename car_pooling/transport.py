"""Employee transport planner.

Assigns m employees (each with their own pickup location) to the minimum
number of cars needed to bring everyone to a common destination.
Each car holds at most `capacity` employees (default 5; the driver is extra).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from numbers import Real

DEFAULT_CAPACITY = 5

Point = tuple[float, float]


class InsufficientCarsError(Exception):
    """Raised when the available cars cannot carry every employee."""


@dataclass(frozen=True)
class Employee:
    id: str
    location: Point


@dataclass
class CarAssignment:
    car_number: int
    employee_ids: list[str]
    route: list[Point]  # pickup points in order, ending at the destination
    distance: float


@dataclass
class TransportPlan:
    cars_used: int
    cars_available: int
    assignments: list[CarAssignment] = field(default_factory=list)

    @property
    def total_distance(self) -> float:
        return sum(a.distance for a in self.assignments)


# ---------------------------------------------------------------------------
# Core calculations
# ---------------------------------------------------------------------------

def _validate_count(value, name: str) -> None:
    # bool is a subclass of int, but True/False are not valid counts
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer, got {type(value).__name__}")
    if value < 0:
        raise ValueError(f"{name} must be non-negative, got {value}")


def _validate_point(point, name: str) -> None:
    if not isinstance(point, (tuple, list)) or len(point) != 2:
        raise ValueError(f"{name} must be an (x, y) pair, got {point!r}")
    for coord in point:
        if isinstance(coord, bool) or not isinstance(coord, Real):
            raise ValueError(f"{name} coordinates must be numbers, got {point!r}")
        if not math.isfinite(coord):
            raise ValueError(f"{name} coordinates must be finite, got {point!r}")


def cars_required(num_employees: int, capacity: int = DEFAULT_CAPACITY) -> int:
    """Minimum number of cars needed to carry `num_employees`."""
    _validate_count(num_employees, "num_employees")
    _validate_count(capacity, "capacity")
    if capacity < 1:
        raise ValueError("capacity must be at least 1")
    return math.ceil(num_employees / capacity)


def _distance(a: Point, b: Point) -> float:
    return math.dist(a, b)


def _order_pickups(locations: list[Point]) -> list[Point]:
    """Nearest-neighbour pickup order starting from the first location.

    The caller sorts the locations (farthest from the destination first),
    so the result is deterministic.
    """
    if not locations:
        return []
    remaining = list(locations)
    route = [remaining.pop(0)]
    while remaining:
        last = route[-1]
        nearest = min(range(len(remaining)), key=lambda i: _distance(last, remaining[i]))
        route.append(remaining.pop(nearest))
    return route


def plan_transport(
    employees: list[Employee],
    num_cars: int,
    destination: Point = (0.0, 0.0),
    capacity: int = DEFAULT_CAPACITY,
) -> TransportPlan:
    """Assign employees to the fewest cars and build each car's route.

    Raises:
        TypeError / ValueError: invalid input.
        InsufficientCarsError: more cars needed than available.
    """
    _validate_count(num_cars, "num_cars")
    _validate_point(destination, "destination")
    if employees is None:
        raise ValueError("employees must be a list, got None")

    seen_ids = set()
    for emp in employees:
        if not isinstance(emp, Employee):
            raise TypeError(f"expected Employee, got {type(emp).__name__}")
        if emp.id in seen_ids:
            raise ValueError(f"duplicate employee id: {emp.id!r}")
        seen_ids.add(emp.id)
        _validate_point(emp.location, f"location of {emp.id}")

    needed = cars_required(len(employees), capacity)
    if needed > num_cars:
        raise InsufficientCarsError(
            f"{len(employees)} employees need {needed} car(s) "
            f"(capacity {capacity}), but only {num_cars} available"
        )

    # Sweep: sort by angle around the destination (then by distance, then id)
    # so that neighbours end up in the same car.
    dx, dy = destination

    def sweep_key(emp: Employee):
        x, y = emp.location[0] - dx, emp.location[1] - dy
        return (math.atan2(y, x), math.hypot(x, y), emp.id)

    ordered = sorted(employees, key=sweep_key)

    assignments = []
    for car_index in range(needed):
        group = ordered[car_index * capacity:(car_index + 1) * capacity]
        # Visit the farthest pickup first, then nearest-neighbour towards home.
        group.sort(key=lambda e: (-_distance(e.location, destination), e.id))
        pickups = _order_pickups([tuple(e.location) for e in group])

        # Map the ordered pickup points back to employee ids (handles duplicates).
        pending = list(group)
        ids_in_order = []
        for point in pickups:
            match = next(e for e in pending if tuple(e.location) == point)
            pending.remove(match)
            ids_in_order.append(match.id)

        route = pickups + [tuple(destination)]
        distance = sum(_distance(route[i], route[i + 1]) for i in range(len(route) - 1))
        assignments.append(CarAssignment(car_index + 1, ids_in_order, route, distance))

    return TransportPlan(cars_used=needed, cars_available=num_cars, assignments=assignments)


# ---------------------------------------------------------------------------
# Command-line interface
# ---------------------------------------------------------------------------

def _read_int(prompt: str, input_fn=input) -> int:
    while True:
        raw = input_fn(prompt).strip()
        try:
            value = int(raw)
        except ValueError:
            print(f"  '{raw}' is not a whole number, try again.")
            continue
        if value < 0:
            print("  Value cannot be negative, try again.")
            continue
        return value


def _read_point(prompt: str, input_fn=input) -> Point:
    while True:
        raw = input_fn(prompt).replace(",", " ").split()
        try:
            if len(raw) != 2:
                raise ValueError
            x, y = float(raw[0]), float(raw[1])
            if not (math.isfinite(x) and math.isfinite(y)):
                raise ValueError
            return (x, y)
        except ValueError:
            print("  Enter two numbers like: 3.5, -2")


def format_plan(plan: TransportPlan) -> str:
    lines = [
        f"Cars required : {plan.cars_used}",
        f"Cars available: {plan.cars_available}",
        f"Cars unused   : {plan.cars_available - plan.cars_used}",
    ]
    for a in plan.assignments:
        stops = " -> ".join(f"({x:g}, {y:g})" for x, y in a.route)
        lines.append(
            f"  Car {a.car_number}: {len(a.employee_ids)} employee(s) {a.employee_ids}"
            f"\n          route: {stops}  [{a.distance:.2f} units]"
        )
    lines.append(f"Total distance: {plan.total_distance:.2f} units")
    return "\n".join(lines)


def main(input_fn=input) -> None:
    print("=== Employee Transport Planner ===")
    m = _read_int("Number of employees: ", input_fn)
    needed = cars_required(m)
    print(f"Minimum cars required for {m} employee(s): {needed}")

    n = _read_int("Number of cars available: ", input_fn)
    if needed > n:
        print(f"Not possible: need {needed} car(s) but only {n} available "
              f"({needed - n} more required).")
        return

    destination = _read_point("Company location (x, y): ", input_fn)

    manual = input_fn("Enter pickup locations manually? (y/n): ").strip().lower() == "y"
    rng = random.Random(42)
    employees = []
    for i in range(1, m + 1):
        if manual:
            loc = _read_point(f"  Pickup location of E{i} (x, y): ", input_fn)
        else:
            loc = (round(rng.uniform(-50, 50), 1), round(rng.uniform(-50, 50), 1))
        employees.append(Employee(f"E{i}", loc))

    plan = plan_transport(employees, n, destination)
    print()
    print(format_plan(plan))


if __name__ == "__main__":
    main()
