# Employee Transport Planner

Transports m employees (each with their own pickup location) to a common
destination (the company) using the minimum number of cars.
Each car carries at most 5 employees; the driver is not counted.

## Run the program

    python3 transport.py

You will be asked for:
1. Number of employees  -> the minimum cars required is printed (ceil(m / 5))
2. Number of cars available  -> stops if there are not enough cars
3. Company location (x, y)
4. Whether to type pickup locations (y) or generate them randomly (n)

## Run the tests

    pip install -r requirements.txt
    python3 -m pytest -v                                  # all tests (success + failure scenarios)
    python3 -m pytest tests/test_failure_scenarios.py -v  # failure scenarios only
    python3 -m pytest tests/demo_failures.py -v           # demo: 4 tests that fail on purpose

When a test fails, the report shows a "Scenario description" (the test's
docstring) and the input, expected result and actual result.

## Layout

    transport.py                    # cars_required(), plan_transport(), interactive main()
    tests/conftest.py               # test helpers + scenario descriptions in failure reports
    tests/test_transport.py         # success scenarios (valid input -> correct plan)
    tests/test_failure_scenarios.py # failure scenarios (invalid input, not enough cars)
    tests/demo_failures.py          # deliberately failing demo, run on its own
  





  