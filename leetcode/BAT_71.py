def performance_summary(students):
    # Write your code here
    result={}
    pass_c=0
    fail_c=0
    max_score=0
    min_score=(float('inf'))
    for val in students.values():
        if val>=50:
            pass_c+=1
        else:
            fail_c+=1
        max_score=max(max_score,val)
        min_score=min(min_score,val)
        result['passed']=pass_c
        result['failed']=fail_c
        result['higest']=max_score
        result['lowest']=min_score
    return result    


# Test your code with the following test cases:
print(performance_summary({'Ravi': 85, 'Priya': 92, 'Kiran': 45, 'Anil': 55, 'Meena': 40, 'John': 70, 'Asha': 88, 'Vijay': 35, 'Arun': 61, 'Neha': 95}))
