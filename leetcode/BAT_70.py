def passed_students(students):
    # Write your code here
    result=[]
    for student,val in students.items():
        if val>=50:
            result.append(student)
    return result        



# Test your code with the following test cases:
print(passed_students({'Ravi': 85, 'Priya': 92, 'Kiran': 45, 'Anil': 55, 'Meena': 40}))
