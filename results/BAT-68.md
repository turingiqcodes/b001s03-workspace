# Assessment Result — BAT-68

**[CODING] Count Positive and Negative Numbers Week1 Tuesday — B001S03**

| Field | Value |
|---|---|
| Candidate | b001s03 (b001s03@training.turingiq.ai) |
| Type | CODING |
| Score | 0/100 |
| Result | **FAIL** |
| Tests passed | 0/3 |
| Execution status | success |
| Submitted at | 2026-09-08T08:21:51.407Z |
| Graded at | 2026-09-08T08:21:56.132134+00:00 |

## Feedback

Hidden tests: 0/3 passed. AI code review: It looks like you haven't implemented the function yet, as the body currently only contains the `pass` statement. To solve the problem, you'll need to iterate through the list and keep track of the counts for positive and negative numbers. Once you add your logic, remember to return both counts from the function.

## Test Case Results

| Call | Expected | Actual | Passed |
|---|---|---|---|
| `count_positive_negative([])` | `[0, 0]` | `None` | ❌ |
| `count_positive_negative([1, 2, 3])` | `[3, 0]` | `None` | ❌ |
| `count_positive_negative([-1, -2, 0])` | `[0, 3]` | `None` | ❌ |

## Your Submitted Code

```python
def count_positive_negative(numbers):
    # Write your code here
    pass

# Test your code with the following test cases:
# count_positive_negative([10, -5, 20, -15, 30, 25, -8, 12, -2, 40])

```

## Reference Solution

```python
def count_positive_negative(numbers):
    index = 0
    positive = 0
    negative = 0

    while index < len(numbers):
        if numbers[index] > 0:
            print(numbers[index])
            positive = positive + 1
        else:
            negative = negative + 1
        index = index + 1

    return [positive, negative]

```

## AI Code Review

It looks like you haven't implemented the function yet, as the body currently only contains the `pass` statement. To solve the problem, you'll need to iterate through the list and keep track of the counts for positive and negative numbers. Once you add your logic, remember to return both counts from the function.
