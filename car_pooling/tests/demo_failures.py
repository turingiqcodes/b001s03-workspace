"""DEMO ONLY: tests with deliberately wrong expectations.

Every test in this file is meant to FAIL, to show what a failure report
looks like. The file name does not start with "test_", so a normal
`pytest` run skips it. Run it explicitly:

    python3 -m pytest tests/demo_failures.py -v
"""

from conftest import make_employees
from test_failure_scenarios import expect_error
from transport import InsufficientCarsError, cars_required, plan_transport


def test_demo_wrong_car_count():
    """DEMO: 12 employees need 3 cars, but this test wrongly expects 2."""
    m, expected = 12, 2
    actual = cars_required(m)
    assert actual == expected, (
        f"\n  Scenario: {m} employees, 5 seats per car"
        f"\n  Expected: {expected} car(s)\n  Actual:   {actual} car(s)"
    )


def test_demo_expects_error_but_plan_succeeds():
    """DEMO: 10 employees fit in 2 cars, but this test wrongly expects a car shortage."""
    expect_error(
        InsufficientCarsError, plan_transport, make_employees(10), 2,
        scenario="10 employees, 2 cars (wrongly expected to be too few)",
    )


def test_demo_wrong_error_type():
    """DEMO: a negative car count raises ValueError, but this test expects TypeError."""
    expect_error(
        TypeError, plan_transport, make_employees(3), -1,
        scenario="number of cars = -1 (wrongly expected TypeError)",
    )


def test_demo_car_over_capacity():
    """DEMO: checks that no car carries more than 4 (the real limit is 5)."""
    plan = plan_transport(make_employees(10), 2)
    for car in plan.assignments:
        assert len(car.employee_ids) <= 4, (
            f"\n  Car {car.car_number} carries {len(car.employee_ids)} employees"
            f"\n  Expected: at most 4\n  Passengers: {car.employee_ids}"
        )
