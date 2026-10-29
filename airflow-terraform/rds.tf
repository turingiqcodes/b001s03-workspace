# Generated so nobody has to type or share a password; letters and digits only so it is safe in a connection URL
resource "random_password" "db" {
  length  = 24
  special = false
}

resource "aws_db_subnet_group" "airflow" {
  name        = "${var.student_id}-airflow-db-subnets"
  description = "Subnets for the Airflow metadata database"
  subnet_ids  = var.db_subnet_ids

  tags = {
    Name = "${var.student_id}-airflow-db-subnets"
  }
}

resource "aws_db_instance" "airflow" {
  identifier     = "${var.student_id}-airflow-db"
  engine         = "postgres"
  engine_version = var.db_engine_version
  instance_class = "db.t3.micro"

  allocated_storage = 20
  db_name           = var.db_name
  username          = var.db_username
  password          = random_password.db.result

  db_subnet_group_name   = aws_db_subnet_group.airflow.name
  vpc_security_group_ids = [aws_security_group.airflow.id]
  publicly_accessible    = false
  multi_az               = false

  # Exercise settings: no backups, nothing left behind on destroy
  backup_retention_period = 0
  skip_final_snapshot     = true
  deletion_protection     = false
  apply_immediately       = true

  tags = {
    Name = "${var.student_id}-airflow-db"
  }
}
