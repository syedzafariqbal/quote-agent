output "user_pool_id" {
  value = aws_cognito_user_pool.this.id
}

output "user_pool_endpoint" {
  value = aws_cognito_user_pool.this.endpoint
}

output "app_client_id" {
  value = aws_cognito_user_pool_client.app.id
}
