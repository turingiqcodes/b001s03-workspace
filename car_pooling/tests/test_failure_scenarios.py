"""Failure scenarios: input the planner must reject or report.

Each test's docstring describes the scenario. When a test fails, the report
shows that description plus the input, the expected result and what
actually happened.
"""

import math
import re

import pytest

from conftest import make_employees
from transport import (
    Employee,
    InsufficientCarsError,
    TransportPlan,
    cars_required,
    main,
    plan_transport,
)


def _describe(result):
    if isinstance(result, TransportPlan):
        return f"a plan using {result.cars_used} car(s)"
    return repr(result)


def expect_error(error, func, *args, scenario, match=None, **kwargs):
    """Call func and require it to raise `error`, with a readable failure message."""
    header = f"FAILED SCENARIO: {scenario}\n  Expected: {error.__name__}"
    if match:
        header += f" with message matching {match!r}"
    # Fail outside the except blocks so the report has no chained traceback.
    raised = None
    try:
        result = func(*args, **kwargs)
    except Exception as exc:
        raised = exc
    if raised is None:
        pytest.fail(f"{header}\n  Actual:   no error, returned {_describe(result)}", pytrace=False)
    actual = f"{type(raised).__name__}: {raised}"
    if not isinstance(raised, error) or (match and not re.search(match, str(raised))):
        pytest.fail(f"{header}\n  Actual:   {actual}", pytrace=False)
    return raised


def feed(*answers):
    """Simulate a user typing the given answers, one per prompt."""
    it = iter(answers)
    return lambda prompt="": next(it)


# ---------------------------------------------------------------------------
# 1. Not enough cars
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "m, n, capacity",
    [
        pytest.param(1, 0, 5, id="1-employee-0-cars"),
        pytest.param(6, 1, 5, id="6-employees-1-car"),
        pytest.param(11, 2, 5, id="11-employees-2-cars"),
        pytest.param(26, 5, 5, id="26-employees-5-cars"),
        pytest.param(51, 10, 5, id="51-employees-10-cars"),
        pytest.param(500, 1, 5, id="500-employees-1-car"),
        pytest.param(5, 4, 1, id="capacity-1-5-employees-4-cars"),
        pytest.param(9, 2, 4, id="capacity-4-9-employees-2-cars"),
    ],
)
def test_insufficient_cars(m, n, capacity):
    """More cars are needed than are available, so planning must fail."""
    needed = math.ceil(m / capacity)
    expect_error(
        InsufficientCarsError, plan_transport, make_employees(m), n, capacity=capacity,
        scenario=f"{m} employees, {n} car(s), {capacity} seat(s) per car -> {needed} car(s) needed",
        match=rf"need {needed} car",
    )


@pytest.mark.parametrize("n", [1, 2, 5, 10])
def test_one_employee_more_than_full_fleet(n):
    """5n + 1 employees cannot fit in n cars of 5 seats."""
    expect_error(
        InsufficientCarsError, plan_transport, make_employees(5 * n + 1), n,
        scenario=f"{5 * n + 1} employees, {n} car(s) (one employee over full capacity)",
    )


def test_large_input_one_car_short():
    """10,000 employees need 2,000 cars; 1,999 is one short."""
    expect_error(
        InsufficientCarsError, plan_transport, make_employees(10_000), 1_999,
        scenario="10,000 employees, 1,999 cars",
    )


def test_insufficient_cars_message_explains_shortfall():
    """The error message must state employees, cars needed and cars available."""
    exc = expect_error(
        InsufficientCarsError, plan_transport, make_employees(30), 4,
        scenario="30 employees, 4 cars",
    )
    msg = str(exc)
    for part in ("30 employees", "6 car", "only 4"):
        assert part in msg, f"Error message {msg!r} is missing {part!r}"


# ---------------------------------------------------------------------------
# 2. Invalid employee / car counts
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("m", [-1, -100])
def test_cars_required_negative_employees(m):
    """A negative number of employees is meaningless."""
    expect_error(ValueError, cars_required, m, scenario=f"number of employees = {m}")


@pytest.mark.parametrize(
    "m", [2.5, "5", None, True, [5]], ids=["float", "string", "None", "bool", "list"]
)
def test_cars_required_non_integer_employees(m):
    """The number of employees must be a whole number."""
    expect_error(TypeError, cars_required, m, scenario=f"number of employees = {m!r}")


@pytest.mark.parametrize("n", [-1, -10])
def test_negative_cars(n):
    """A negative number of cars is meaningless."""
    expect_error(
        ValueError, plan_transport, make_employees(3), n, scenario=f"number of cars = {n}"
    )


@pytest.mark.parametrize("n", [1.5, "3", None, True], ids=["float", "string", "None", "bool"])
def test_non_integer_cars(n):
    """The number of cars must be a whole number."""
    expect_error(
        TypeError, plan_transport, make_employees(3), n, scenario=f"number of cars = {n!r}"
    )


@pytest.mark.parametrize("capacity", [0, -5])
def test_invalid_capacity(capacity):
    """A car must have at least one seat."""
    expect_error(
        ValueError, cars_required, 10, capacity, scenario=f"capacity = {capacity}"
    )
    expect_error(
        ValueError, plan_transport, make_employees(3), 3, capacity=capacity,
        scenario=f"plan_transport with capacity = {capacity}",
    )


def test_non_integer_capacity():
    """Seats per car must be a whole number."""
    expect_error(TypeError, cars_required, 10, "5", scenario="capacity = '5'")


# ---------------------------------------------------------------------------
# 3. Invalid employee data
# ---------------------------------------------------------------------------

def test_employee_list_is_none():
    """The employee list is missing entirely."""
    expect_error(ValueError, plan_transport, None, 3, scenario="employees = None")


def test_employee_is_not_an_employee_object():
    """A plain tuple is passed instead of an Employee."""
    expect_error(
        TypeError, plan_transport, [("E1", (1, 2))], 1,
        scenario="employees = [('E1', (1, 2))]",
    )


def test_duplicate_employee_ids():
    """Two employees share the same id, so assignments would be ambiguous."""
    emps = [Employee("E1", (1, 1)), Employee("E1", (2, 2))]
    expect_error(
        ValueError, plan_transport, emps, 1,
        scenario="two employees with id 'E1'", match="duplicate",
    )


@pytest.mark.parametrize(
    "location",
    [None, (1,), (1, 2, 3), ("a", 2), (1, None), (float("nan"), 0), (0, float("inf")), (True, 1), "1,2"],
    ids=["None", "one-value", "three-values", "text", "None-coord", "NaN", "infinity", "bool", "string"],
)
def test_invalid_pickup_location(location):
    """A pickup location must be two finite numbers (x, y)."""
    expect_error(
        ValueError, plan_transport, [Employee("E1", location)], 1,
        scenario=f"pickup location = {location!r}",
    )


@pytest.mark.parametrize(
    "destination", [None, (1,), ("x", "y"), (float("nan"), 0)],
    ids=["None", "one-value", "text", "NaN"],
)
def test_invalid_company_location(destination):
    """The company location must be two finite numbers (x, y)."""
    expect_error(
        ValueError, plan_transport, make_employees(2), 1, destination=destination,
        scenario=f"company location = {destination!r}",
    )


def test_bad_data_reported_before_car_shortage():
    """Invalid data is reported even when there are also too few cars."""
    emps = [Employee("E1", (1, 1)), Employee("E1", (2, 2))]
    expect_error(
        ValueError, plan_transport, emps, 0,
        scenario="duplicate ids AND 0 cars (data error should win)",
    )


def test_failed_plan_does_not_modify_input():
    """A rejected request must leave the caller's employee list untouched."""
    emps = make_employees(11)
    snapshot = list(emps)
    expect_error(InsufficientCarsError, plan_transport, emps, 2, scenario="11 employees, 2 cars")
    assert emps == snapshot, "Employee list was changed by a failed plan_transport call"


# ---------------------------------------------------------------------------
# 4. Interactive program (user typing bad values)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "m, n, needed",
    [(11, 2, 3), (6, 0, 2), (100, 19, 20)],
    ids=["11-employees-2-cars", "6-employees-0-cars", "100-employees-19-cars"],
)
def test_main_reports_not_enough_cars(capsys, m, n, needed):
    """The program explains how many more cars are needed instead of crashing."""
    main(feed(str(m), str(n)))
    out = capsys.readouterr().out
    expected = f"Not possible: need {needed} car(s) but only {n} available ({needed - n} more required)"
    assert expected in out, f"\n  Expected output to contain: {expected!r}\n  Actual output:\n{out}"


@pytest.mark.parametrize(
    "bad_answer, expected_message",
    [
        ("abc", "'abc' is not a whole number"),
        ("5.5", "'5.5' is not a whole number"),
        ("", "'' is not a whole number"),
        ("-3", "cannot be negative"),
    ],
    ids=["text", "decimal", "blank", "negative"],
)
def test_main_reprompts_on_bad_employee_count(capsys, bad_answer, expected_message):
    """A bad employee count shows an error and asks again."""
    main(feed(bad_answer, "6", "2", "0,0", "n"))
    out = capsys.readouterr().out
    assert expected_message in out, (
        f"\n  Typed: {bad_answer!r}\n  Expected message: {expected_message!r}\n  Actual output:\n{out}"
    )
    assert "Cars required : 2" in out, "Program did not continue after a valid answer"


@pytest.mark.parametrize(
    "bad_point", ["x", "1 2 3", "5", "nan, 0", "inf 1"],
    ids=["text", "three-values", "one-value", "NaN", "infinity"],
)
def test_main_reprompts_on_bad_coordinates(capsys, bad_point):
    """A bad company location shows an error and asks again."""
    main(feed("2", "1", bad_point, "0,0", "n"))
    out = capsys.readouterr().out
    assert "Enter two numbers" in out, (
        f"\n  Typed location: {bad_point!r}\n  Expected a re-prompt\n  Actual output:\n{out}"
    )
    assert "Cars required : 1" in out, "Program did not continue after a valid location"
