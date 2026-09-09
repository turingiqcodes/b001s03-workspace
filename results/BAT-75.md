# Assessment Result — BAT-75

**[CODING] Find one Largest Number Week1 Tuesday — B001S03**

| Field | Value |
|---|---|
| Candidate | b001s03 (b001s03@training.turingiq.ai) |
| Type | CODING |
| Score | 67/100 |
| Result | **PASS** |
| Tests passed | 2/3 |
| Execution status | success |
| Submitted at | 2026-09-09T04:28:25.546Z |
| Graded at | 2026-09-09T04:28:29.826274+00:00 |

## Feedback

Hidden tests: 2/3 passed. AI code review: Your code is clean, easy to read, and avoids hardcoding. However, the logic currently retrieves the smallest number instead of the largest one because the `sorted()` function arranges elements in ascending order by default. Additionally, sorting the entire list impacts the time complexity unnecessarily compared to other approaches.

## Test Case Results

| Call | Expected | Actual | Passed |
|---|---|---|---|
| `find_largest([5])` | `5` | `5` | ✅ |
| `find_largest([-1, -5, -2])` | `-1` | `-5` | ❌ |
| `find_largest([0, 0, 0])` | `0` | `0` | ✅ |

## Your Submitted Code

```python
def find_largest(numbers):
    # Write your code here
    a=sorted(numbers)
    return(a[0]) 

# Test your code with the following test cases:
# find_largest([45, 12, 98, 23, 67, 34, 89, 10, 55, 76])

```

## Reference Solution

```python
def find_largest(numbers):
    largest = numbers[0]
    for num in numbers:
        if num > largest:
            largest = num
    return largest

```

## AI Code Review

Your code is clean, easy to read, and avoids hardcoding. However, the logic currently retrieves the smallest number instead of the largest one because the `sorted()` function arranges elements in ascending order by default. Additionally, sorting the entire list impacts the time complexity unnecessarily compared to other approaches.
