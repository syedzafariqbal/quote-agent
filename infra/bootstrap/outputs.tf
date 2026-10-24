output "state_bucket" {
  value = aws_s3_bucket.state.bucket
}

output "gha_role_arn" {
  description = "Put this in the GitHub repository variable AWS_ROLE_ARN"
  value       = aws_iam_role.gha_deploy.arn
}
