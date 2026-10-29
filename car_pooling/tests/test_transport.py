"""Success scenarios: valid input that must produce a correct plan.

Error and invalid-input scenarios live in test_failure_scenarios.py.
"""

import math
import time
from collections import Counter

import pytest

from conftest import make_employees
from transport import (
    CarAssignment,
    Employee,
    TransportPlan,
    cars_required,
    format_plan,
    main,
    plan_transport,
)


def assert_valid_plan(plan, employees, num_cars, destination=(0.0, 0.0), capacity=5):
    """Check every invariant a correct plan must satisfy."""
    assigned = [eid for a in plan.assignments for eid in a.employee_ids]

    # Every employee assigned exactly once
    assert Counter(assigned) == Counter(e.id for e in employees)
    # Minimum number of cars, never more than available
    assert plan.cars_used == math.ceil(len(employees) / capacity)
    assert plan.cars_used <= num_cars
    assert len(plan.assignments) == plan.cars_used
    # Capacity respected, no empty cars
    for a in plan.assignments:
        assert 1 <= len(a.employee_ids) <= capacity
    # Routes: one stop per passenger, ending at the destination
    by_id = {e.id: tuple(e.location) for e in employees}
    for a in plan.assignments:
        assert a.route[-1] == tuple(destination)
        assert len(a.route) == len(a.employee_ids) + 1
        assert a.route[:-1] == [by_id[eid] for eid in a.employee_ids]
        assert a.distance >= 0


# ---------------------------------------------------------------------------
# 1. cars_required (the number calculated from the user's input)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "m, expected",
    [
        (0, 0),
        (1, 1),
        (4, 1),
        (5, 1),
        (6, 2),
        (10, 2),
        (11, 3),
        (24, 5),
        (25, 5),
        (26, 6),
        (100, 20),
        (101, 21),
        (999_999, 200_000),
    ],
)
def test_cars_required(m, expected):
    assert cars_required(m) == expected


@pytest.mark.parametrize(
    "m, capacity, expected",
    [(7, 1, 7), (7, 4, 2), (8, 4, 2), (9, 4, 3), (7, 7, 1), (15, 7, 3), (0, 3, 0)],
)
def test_cars_required_custom_capacity(m, capacity, expected):
    assert cars_required(m, capacity) == expected


# ---------------------------------------------------------------------------
# 2. Basic scenarios
# ---------------------------------------------------------------------------

def test_single_employee_single_car():
    emps = [Employee("E1", (3, 4))]
    plan = plan_transport(emps, 1)
    assert_valid_plan(plan, emps, 1)
    assert plan.assignments[0].distance == pytest.approx(5.0)


def test_exactly_five_fit_in_one_car():
    emps = make_employees(5)
    plan = plan_transport(emps, 1)
    assert plan.cars_used == 1
    assert_valid_plan(plan, emps, 1)


def test_six_employees_need_two_cars():
    emps = make_employees(6)
    plan = plan_transport(emps, 2)
    assert plan.cars_used == 2
    assert sorted(len(a.employee_ids) for a in plan.assignments) == [1, 5]


def test_extra_cars_are_left_unused():
    emps = make_employees(12)
    plan = plan_transport(emps, 10)
    assert plan.cars_used == 3
    assert plan.cars_available == 10
    assert_valid_plan(plan, emps, 10)


def test_cars_are_filled_before_opening_a_new_one():
    emps = make_employees(23)
    plan = plan_transport(emps, 5)
    sizes = [len(a.employee_ids) for a in plan.assignments]
    assert sizes == [5, 5, 5, 5, 3]


def test_car_numbers_are_sequential():
    plan = plan_transport(make_employees(17), 4)
    assert [a.car_number for a in plan.assignments] == [1, 2, 3, 4]


# ---------------------------------------------------------------------------
# 3. Boundary scenarios
# ---------------------------------------------------------------------------

def test_zero_employees_needs_zero_cars():
    plan = plan_transport([], 3)
    assert plan.cars_used == 0
    assert plan.assignments == []
    assert plan.total_distance == 0


def test_zero_employees_zero_cars():
    plan = plan_transport([], 0)
    assert plan.cars_used == 0


@pytest.mark.parametrize("n", [1, 2, 5, 10])
def test_exactly_full_fleet(n):
    emps = make_employees(5 * n)
    plan = plan_transport(emps, n)
    assert plan.cars_used == n
    assert all(len(a.employee_ids) == 5 for a in plan.assignments)


# ---------------------------------------------------------------------------
# 4. Invariants across many combinations of m and n
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("m", [0, 1, 2, 3, 4, 5, 6, 9, 10, 11, 14, 15, 16, 49, 50, 51])
@pytest.mark.parametrize("extra_cars", [0, 1, 7])
def test_invariants_hold(m, extra_cars):
    n = math.ceil(m / 5) + extra_cars
    emps = make_employees(m, seed=m)
    assert_valid_plan(plan_transport(emps, n), emps, n)


@pytest.mark.parametrize("seed", range(25))
def test_random_inputs_satisfy_invariants(seed):
    import random

    rng = random.Random(seed)
    m = rng.randint(0, 200)
    n = math.ceil(m / 5) + rng.randint(0, 10)
    dest = (rng.uniform(-500, 500), rng.uniform(-500, 500))
    emps = make_employees(m, seed=seed, spread=1000)
    assert_valid_plan(plan_transport(emps, n, dest), emps, n, dest)


# ---------------------------------------------------------------------------
# 5. Location scenarios
# ---------------------------------------------------------------------------

def test_all_employees_at_same_location():
    emps = [Employee(f"E{i}", (10, 10)) for i in range(1, 8)]
    plan = plan_transport(emps, 2)
    assert_valid_plan(plan, emps, 2)
    for a in plan.assignments:
        assert a.distance == pytest.approx(math.hypot(10, 10))


def test_employee_already_at_destination():
    emps = [Employee("E1", (0, 0)), Employee("E2", (3, 4))]
    plan = plan_transport(emps, 1)
    assert_valid_plan(plan, emps, 1)
    # Pick up E2 first (farther), then E1 at the office: 5 + 0
    assert plan.assignments[0].employee_ids == ["E2", "E1"]
    assert plan.assignments[0].distance == pytest.approx(5.0)


def test_all_employees_at_destination():
    emps = [Employee(f"E{i}", (2, 2)) for i in range(1, 4)]
    plan = plan_transport(emps, 1, destination=(2, 2))
    assert plan.total_distance == pytest.approx(0.0)


def test_negative_and_float_coordinates():
    emps = [
        Employee("A", (-1.5, -2.25)),
        Employee("B", (-100, 50.75)),
        Employee("C", (0.001, -0.001)),
    ]
    plan = plan_transport(emps, 1)
    assert_valid_plan(plan, emps, 1)


def test_collinear_locations():
    emps = [Employee(f"E{i}", (i, 0)) for i in range(1, 6)]
    plan = plan_transport(emps, 1)
    # Farthest first, then walk straight home: 5 -> 4 -> ... -> 1 -> 0
    assert plan.assignments[0].employee_ids == ["E5", "E4", "E3", "E2", "E1"]
    assert plan.assignments[0].distance == pytest.approx(5.0)


def test_non_origin_destination():
    emps = make_employees(13)
    dest = (250.0, -75.0)
    plan = plan_transport(emps, 3, destination=dest)
    assert_valid_plan(plan, emps, 3, dest)


def test_very_large_coordinates():
    emps = [Employee("E1", (1e9, 1e9)), Employee("E2", (-1e9, -1e9))]
    plan = plan_transport(emps, 1)
    assert_valid_plan(plan, emps, 1)
    assert math.isfinite(plan.total_distance)


def test_list_location_is_accepted():
    emps = [Employee("E1", [3, 4])]
    plan = plan_transport(emps, 1)
    assert plan.assignments[0].route == [(3, 4), (0.0, 0.0)]


# ---------------------------------------------------------------------------
# 6. Grouping quality (neighbours share a car)
# ---------------------------------------------------------------------------

def test_two_far_apart_clusters_go_in_separate_cars():
    east = [Employee(f"East{i}", (100 + i, i)) for i in range(5)]
    west = [Employee(f"West{i}", (-100 - i, i)) for i in range(5)]
    plan = plan_transport(east + west, 2)
    groups = [set(a.employee_ids) for a in plan.assignments]
    assert {e.id for e in east} in groups
    assert {e.id for e in west} in groups


def test_four_clusters_in_four_directions():
    clusters = {
        "N": (0, 100), "S": (0, -100), "E": (100, 0), "W": (-100, 0),
    }
    emps = [
        Employee(f"{name}{i}", (cx + i * 0.1, cy + i * 0.1))
        for name, (cx, cy) in clusters.items()
        for i in range(5)
    ]
    plan = plan_transport(emps, 4)
    for a in plan.assignments:
        # Every car contains a single cluster
        assert len({eid[0] for eid in a.employee_ids}) == 1


def test_grouping_beats_naive_order():
    # Input order alternates between two opposite clusters; a naive
    # "first five in the list" split would mix them.
    emps = []
    for i in range(5):
        emps.append(Employee(f"E{i}", (50 + i, 0)))
        emps.append(Employee(f"W{i}", (-50 - i, 0)))
    plan = plan_transport(emps, 2)
    naive = 0.0
    for chunk in (emps[:5], emps[5:]):
        pts = [e.location for e in chunk] + [(0, 0)]
        naive += sum(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))
    assert plan.total_distance < naive


# ---------------------------------------------------------------------------
# 7. Custom capacity
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("capacity", [1, 2, 4, 7, 10])
def test_custom_capacity(capacity):
    m = 23
    n = math.ceil(m / capacity)
    emps = make_employees(m)
    plan = plan_transport(emps, n, capacity=capacity)
    assert_valid_plan(plan, emps, n, capacity=capacity)


def test_capacity_one_means_one_car_per_employee():
    emps = make_employees(4)
    plan = plan_transport(emps, 4, capacity=1)
    assert plan.cars_used == 4


# ---------------------------------------------------------------------------
# 8. Determinism and input safety
# ---------------------------------------------------------------------------

def test_same_input_gives_same_plan():
    emps = make_employees(37, seed=7)
    a = plan_transport(emps, 10)
    b = plan_transport(emps, 10)
    assert [x.employee_ids for x in a.assignments] == [x.employee_ids for x in b.assignments]


def test_input_order_does_not_change_grouping():
    emps = make_employees(37, seed=7)
    a = plan_transport(emps, 8)
    b = plan_transport(list(reversed(emps)), 8)
    assert [set(x.employee_ids) for x in a.assignments] == [set(x.employee_ids) for x in b.assignments]


def test_input_list_not_modified():
    emps = make_employees(12)
    snapshot = list(emps)
    plan_transport(emps, 3)
    assert emps == snapshot


# ---------------------------------------------------------------------------
# 9. Scale
# ---------------------------------------------------------------------------

def test_large_input_is_fast():
    emps = make_employees(10_000, seed=1, spread=10_000)
    start = time.perf_counter()
    plan = plan_transport(emps, 2_000)
    elapsed = time.perf_counter() - start
    assert plan.cars_used == 2_000
    assert_valid_plan(plan, emps, 2_000)
    assert elapsed < 2.0


# ---------------------------------------------------------------------------
# 10. Output formatting and the interactive program (user input)
# ---------------------------------------------------------------------------

def test_total_distance_is_sum_of_cars():
    plan = plan_transport(make_employees(18), 4)
    assert plan.total_distance == pytest.approx(sum(a.distance for a in plan.assignments))


def test_format_plan_lists_every_car():
    plan = TransportPlan(
        cars_used=1,
        cars_available=3,
        assignments=[CarAssignment(1, ["E1"], [(3, 4), (0, 0)], 5.0)],
    )
    text = format_plan(plan)
    assert "Cars required : 1" in text
    assert "Cars unused   : 2" in text
    assert "Car 1" in text and "5.00" in text


def feed(*answers):
    """Simulate a user typing the given answers, one per prompt."""
    it = iter(answers)
    return lambda prompt="": next(it)


def test_main_random_locations(capsys):
    main(feed("12", "5", "0,0", "n"))
    out = capsys.readouterr().out
    assert "Minimum cars required for 12 employee(s): 3" in out
    assert "Cars unused   : 2" in out
    assert out.count("Car ") == 3


def test_main_manual_locations(capsys):
    main(feed("2", "1", "0 0", "y", "3,4", "6 8"))
    out = capsys.readouterr().out
    assert "Cars required : 1" in out
    assert "Total distance: 10.00" in out


def test_main_zero_employees(capsys):
    main(feed("0", "0", "0,0", "n"))
    out = capsys.readouterr().out
    assert "Minimum cars required for 0 employee(s): 0" in out


