import inspect
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from transport import Employee  # noqa: E402


def make_employees(m, seed=0, spread=100.0):
    """Generate m employees at random (but reproducible) locations."""
    rng = random.Random(seed)
    return [
        Employee(f"E{i}", (rng.uniform(-spread, spread), rng.uniform(-spread, spread)))
        for i in range(1, m + 1)
    ]


@pytest.fixture
def employees_factory():
    return make_employees


@pytest.fixture
def destination():
    return (0.0, 0.0)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Add the failing test's docstring to its report as a scenario description."""
    outcome = yield
    report = outcome.get_result()
    doc = getattr(getattr(item, "function", None), "__doc__", None)
    if report.failed and call.when == "call" and doc:
        report.sections.append(("Scenario description", inspect.cleandoc(doc)))
