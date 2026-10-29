resource "random_password" "airflow_admin" {
  length  = 16
  special = false
}

resource "aws_key_pair" "sandbox" {
  key_name   = "${var.student_id}-sandbox-key"
  public_key = file(pathexpand(var.ssh_public_key_path))

  tags = {
    Name = "${var.student_id}-sandbox-key"
  }
}

resource "aws_instance" "airflow" {
  ami                         = var.ami_id
  instance_type               = var.instance_type
  subnet_id                   = var.public_subnet_id
  vpc_security_group_ids      = [aws_security_group.airflow.id]
  key_name                    = aws_key_pair.sandbox.key_name
  associate_public_ip_address = true

  user_data = templatefile("${path.module}/airflow_setup.sh.tftpl", {
    airflow_version = var.airflow_version
    db_host         = aws_db_instance.airflow.address
    db_port         = aws_db_instance.airflow.port
    db_name         = var.db_name
    db_username     = var.db_username
    db_password     = random_password.db.result
    admin_username  = "admin"
    admin_password  = random_password.airflow_admin.result
  })
  user_data_replace_on_change = true

  metadata_options {
    http_tokens = "required"
  }

  root_block_device {
    volume_size = 20
    volume_type = "gp3"
    tags = merge(local.common_tags, {
      Name = "${var.student_id}-airflow-root"
    })
  }

  tags = {
    Name = "${var.student_id}-airflow"
  }
}
