# Assessment Result — BAT-71

**[CODING] Student Performance Summary Week1 Tuesday — B001S03**

| Field | Value |
|---|---|
| Candidate | b001s03 (b001s03@training.turingiq.ai) |
| Type | CODING |
| Score | 0/100 |
| Result | **FAIL** |
| Tests passed | 0/2 |
| Execution status | success |
| Submitted at | 2026-09-08T09:00:23.746Z |
| Graded at | 2026-09-08T09:00:29.694213+00:00 |

## Feedback

Hidden tests: 0/2 passed. AI code review: The submitted code is currently just a placeholder containing the `pass` statement, meaning no logic has been implemented yet. To complete the task, you will need to iterate through the dictionary to calculate the required performance metrics such as averages, maximums, and minimums. Once implemented, ensure your solution handles the data dynamically without hardcoding any specific student names or scores.

## Test Case Results

| Call | Expected | Actual | Passed |
|---|---|---|---|
| `performance_summary({'A': 50})` | `{'passed': 1, 'failed': 0, 'highest': 50, 'lowest': 50}` | `None` | ❌ |
| `performance_summary({'A': 10, 'B': 20})` | `{'passed': 0, 'failed': 2, 'highest': 20, 'lowest': 10}` | `None` | ❌ |

## Your Submitted Code

```python
def performance_summary(students):
    # Write your code here
    pass

# Test your code with the following test cases:
# performance_summary({'Ravi': 85, 'Priya': 92, 'Kiran': 45, 'Anil': 55, 'Meena': 40, 'John': 70, 'Asha': 88, 'Vijay': 35, 'Arun': 61, 'Neha': 95})

```

## Reference Solution

```python
def performance_summary(students):
    passed = 0
    failed = 0

    marks = list(students.values())
    highest = marks[0]
    lowest = marks[0]

    for mark in marks:
        if mark >= 50:
            passed += 1
        else:
            failed += 1

        if mark > highest:
            highest = mark
        if mark < lowest:
            lowest = mark

    return {"passed": passed, "failed": failed, "highest": highest, "lowest": lowest}

```

## AI Code Review

The submitted code is currently just a placeholder containing the `pass` statement, meaning no logic has been implemented yet. To complete the task, you will need to iterate through the dictionary to calculate the required performance metrics such as averages, maximums, and minimums. Once implemented, ensure your solution handles the data dynamically without hardcoding any specific student names or scores.
