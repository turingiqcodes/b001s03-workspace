student_id       = "b001s03"
region           = "us-east-1"
vpc_id           = "vpc-0f51fca4df43aff8e"
public_subnet_id = "subnet-07dda9e4b38dc3fc3" # public, us-east-1a
db_subnet_ids = [
  "subnet-05fe3c34f55a6794f", # private, us-east-1a
  "subnet-0aa1c1f372130015c", # private, us-east-1b
]
ami_id        = "ami-062944a84867f2386" # Ubuntu 22.04, 2026-10-01 build
my_ip         = "54.92.149.88/32"
instance_type = "t3.medium"
