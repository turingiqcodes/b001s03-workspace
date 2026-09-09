def find_largest(numbers):
    # Write your code here
    initial_value=numbers[0]
    for val in numbers:
        if val>initial_value:
            initial_value=val
    return initial_value        


# Test your code with the following test cases:
print(find_largest([45, 12, 98, 23, 67, 34, 89, 10, 55, 76]))
