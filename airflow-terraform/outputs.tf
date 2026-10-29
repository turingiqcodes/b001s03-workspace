output "instance_id" {
  value = aws_instance.airflow.id
}

output "instance_public_ip" {
  description = "Changes after a stop/start; run terraform apply -refresh-only to update it"
  value       = aws_instance.airflow.public_ip
}

output "airflow_url" {
  value = "http://${aws_instance.airflow.public_ip}:8080"
}

output "ssh_command" {
  value = "ssh -i ~/.ssh/sandbox_key ubuntu@${aws_instance.airflow.public_ip}"
}

output "rds_endpoint" {
  value = aws_db_instance.airflow.endpoint
}

output "airflow_admin_username" {
  value = "admin"
}

output "airflow_admin_password" {
  description = "Read it with: terraform output -raw airflow_admin_password"
  value       = random_password.airflow_admin.result
  sensitive   = true
}
