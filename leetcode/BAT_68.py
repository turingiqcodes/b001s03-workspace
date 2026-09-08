def count_positive_negative(numbers):
    # Write your code here
    positive_count=0
    negative_count=0
    i=0
    while i<len(numbers):
        if numbers[i]>0:
            positive_count+=1
            print(numbers[i])
        else:
            negative_count+=1
        i+=1
    return positive_count,negative_count


# Test your code with the following test cases:
print(count_positive_negative([10, -5, 20, -15, 30, 25, -8, 12, -2, 40]))

