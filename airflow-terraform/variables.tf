variable "student_id" {
  description = "Your student ID in lowercase, e.g. b001s03"
  type        = string

  validation {
    condition     = can(regex("^b001[a-z0-9]+$", var.student_id))
    error_message = "student_id must be lowercase and start with b001."
  }
}

variable "region" {
  description = "AWS region to build in"
  type        = string
  default     = "us-east-1"
}

variable "vpc_id" {
  description = "ID of the training VPC"
  type        = string
}

variable "public_subnet_id" {
  description = "Public subnet (route to an internet gateway) for the EC2 instance"
  type        = string
}

variable "db_subnet_ids" {
  description = "At least two subnets in different availability zones for the RDS subnet group"
  type        = list(string)

  validation {
    condition     = length(var.db_subnet_ids) >= 2
    error_message = "RDS needs at least two subnets in different availability zones."
  }
}

variable "ami_id" {
  description = "Ubuntu 22.04 AMI ID"
  type        = string
}

variable "my_ip" {
  description = "Your public IP in CIDR form, e.g. 203.0.113.10/32"
  type        = string
}

variable "instance_type" {
  description = "EC2 instance type (t3.small or larger)"
  type        = string
  default     = "t3.medium"
}

variable "ssh_public_key_path" {
  description = "Path to the SSH public key that will be installed on the instance"
  type        = string
  default     = "~/.ssh/sandbox_key.pub"
}

variable "airflow_version" {
  description = "Apache Airflow version to install"
  type        = string
  default     = "2.10.5"
}

variable "db_name" {
  description = "Name of the Airflow metadata database"
  type        = string
  default     = "airflow"
}

variable "db_username" {
  description = "Master username for the RDS database"
  type        = string
  default     = "airflow"
}

variable "db_engine_version" {
  description = "Postgres engine version; null lets AWS pick its default"
  type        = string
  default     = null
}
