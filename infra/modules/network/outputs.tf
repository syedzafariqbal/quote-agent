output "vpc_id" {
  value = aws_vpc.this.id
}

output "subnet_ids" {
  value = aws_subnet.public[*].id
}

output "worker_security_group_id" {
  value = aws_security_group.worker.id
}
