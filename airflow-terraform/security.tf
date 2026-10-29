resource "aws_security_group" "airflow" {
  name        = "${var.student_id}-airflow-sg"
  description = "SSH and Airflow UI from my IP; Postgres between members of this group"
  vpc_id      = var.vpc_id

  ingress {
    description = "SSH from my IP"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.my_ip]
  }

  ingress {
    description = "Airflow web UI from my IP"
    from_port   = 8080
    to_port     = 8080
    protocol    = "tcp"
    cidr_blocks = [var.my_ip]
  }

  # The instance and the database both use this group, so this lets the instance reach Postgres
  ingress {
    description = "Postgres between the Airflow instance and RDS"
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    self        = true
  }

  egress {
    description = "All outbound traffic (package installs, database)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${var.student_id}-airflow-sg"
  }
}
