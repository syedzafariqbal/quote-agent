output "ecr_repository_url" {
  value = aws_ecr_repository.worker.repository_url
}

output "ecr_repository_name" {
  value = aws_ecr_repository.worker.name
}

output "cluster_name" {
  value = aws_ecs_cluster.this.name
}

output "service_name" {
  value = aws_ecs_service.worker.name
}

output "task_role_name" {
  value = aws_iam_role.task.name
}

output "log_group" {
  value = aws_cloudwatch_log_group.worker.name
}
