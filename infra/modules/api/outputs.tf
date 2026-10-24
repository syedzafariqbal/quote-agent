output "api_endpoint" {
  value = aws_apigatewayv2_api.http.api_endpoint
}

output "api_function_name" {
  value = aws_lambda_function.api.function_name
}

output "link_secret_param" {
  value = aws_ssm_parameter.link_secret.name
}
