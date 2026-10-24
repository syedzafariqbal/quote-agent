# HTTP API (cheaper than REST API) + Python Lambdas:
#   api        - lifecycle endpoints (JWT) and public customer pages (HMAC link)
#   scheduler  - reminder / expiry callbacks from EventBridge Scheduler
#   archiver   - DynamoDB stream -> audit bucket

data "aws_region" "current" {}

locals {
  region = data.aws_region.current.region
}

# ---------------------------------------------------------------- package
data "archive_file" "lambda" {
  type        = "zip"
  source_dir  = var.source_dir
  output_path = "${path.root}/.build/lambda.zip"
  excludes    = ["worker", "worker/__init__.py", "worker/main.py", "worker/pipeline.py"]
}

# ---------------------------------------------------------------- link secret
resource "random_password" "link_secret" {
  length  = 48
  special = false
}

resource "aws_ssm_parameter" "link_secret" {
  name        = "/${var.name}/link-secret"
  description = "HMAC key for customer quote links"
  type        = "SecureString"
  value       = random_password.link_secret.result
}

# ---------------------------------------------------------------- SES sender
resource "aws_sesv2_email_identity" "sender" {
  count          = var.sender_email == "" ? 0 : 1
  email_identity = var.sender_email
}

# ---------------------------------------------------------------- IAM: API lambda
data "aws_iam_policy_document" "lambda_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "api" {
  name               = "${var.name}-api-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_trust.json
}

resource "aws_iam_role_policy_attachment" "api_logs" {
  role       = aws_iam_role.api.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "api" {
  statement {
    sid = "Table"
    actions = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:Query",
    "dynamodb:ConditionCheckItem"]
    resources = [var.table_arn, "${var.table_arn}/index/*"]
  }
  statement {
    sid       = "Queue"
    actions   = ["sqs:SendMessage"]
    resources = [var.queue_arn]
  }
  statement {
    sid       = "ReadPdfs"
    actions   = ["s3:GetObject"]
    resources = ["${var.quotes_bucket_arn}/*"]
  }
  statement {
    sid       = "Events"
    actions   = ["events:PutEvents"]
    resources = [var.event_bus_arn]
  }
  statement {
    sid       = "Schedules"
    actions   = ["scheduler:CreateSchedule"]
    resources = ["arn:aws:scheduler:${local.region}:${var.account_id}:schedule/${var.scheduler_group_name}/*"]
  }
  statement {
    sid       = "PassSchedulerRole"
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.scheduler.arn]
  }
  statement {
    sid       = "LinkSecret"
    actions   = ["ssm:GetParameter"]
    resources = [aws_ssm_parameter.link_secret.arn]
  }
  statement {
    sid       = "DecryptSsm"
    actions   = ["kms:Decrypt"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ssm.${local.region}.amazonaws.com"]
    }
  }
  statement {
    sid       = "Email"
    actions   = ["ses:SendEmail"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "api" {
  name   = "${var.name}-api"
  role   = aws_iam_role.api.id
  policy = data.aws_iam_policy_document.api.json
}

# ---------------------------------------------------------------- IAM: scheduler + its lambda
data "aws_iam_policy_document" "scheduler_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.account_id]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  name               = "${var.name}-scheduler-invoke"
  assume_role_policy = data.aws_iam_policy_document.scheduler_trust.json
}

resource "aws_iam_role_policy" "scheduler_invoke" {
  name = "${var.name}-scheduler-invoke"
  role = aws_iam_role.scheduler.id
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "lambda:InvokeFunction", Resource = aws_lambda_function.scheduler.arn }]
  })
}

resource "aws_iam_role" "scheduler_fn" {
  name               = "${var.name}-scheduler-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_trust.json
}

resource "aws_iam_role_policy_attachment" "scheduler_fn_logs" {
  role       = aws_iam_role.scheduler_fn.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "scheduler_fn" {
  name = "${var.name}-scheduler-fn"
  role = aws_iam_role.scheduler_fn.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["dynamodb:GetItem", "dynamodb:UpdateItem", "dynamodb:PutItem"], Resource = var.table_arn },
      { Effect = "Allow", Action = "events:PutEvents", Resource = var.event_bus_arn },
      { Effect = "Allow", Action = "ses:SendEmail", Resource = "*" },
    ]
  })
}

# ---------------------------------------------------------------- IAM: archiver
resource "aws_iam_role" "archiver" {
  name               = "${var.name}-audit-archiver"
  assume_role_policy = data.aws_iam_policy_document.lambda_trust.json
}

resource "aws_iam_role_policy_attachment" "archiver_logs" {
  role       = aws_iam_role.archiver.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "archiver" {
  name = "${var.name}-audit-archiver"
  role = aws_iam_role.archiver.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["dynamodb:DescribeStream", "dynamodb:GetRecords", "dynamodb:GetShardIterator", "dynamodb:ListStreams"], Resource = var.table_stream_arn },
      { Effect = "Allow", Action = "s3:PutObject", Resource = "${var.audit_bucket_arn}/audit/*" },
    ]
  })
}

# ---------------------------------------------------------------- functions
locals {
  common_env = {
    TABLE_NAME        = var.table_name
    EVENT_BUS_NAME    = var.event_bus_name
    SENDER_EMAIL      = var.sender_email
    APPROVER_EMAIL    = var.approver_email
    POWERTOOLS_LOG    = "INFO"
    MAX_RECEIVE_COUNT = tostring(var.max_receive_count)
  }
}

resource "aws_cloudwatch_log_group" "fn" {
  for_each          = toset(["api", "scheduler", "archiver"])
  name              = "/aws/lambda/${var.name}-${each.key}"
  retention_in_days = var.log_retention_days
}

resource "aws_lambda_function" "api" {
  function_name    = "${var.name}-api"
  role             = aws_iam_role.api.arn
  runtime          = "python3.12"
  architectures    = ["arm64"]
  handler          = "handlers.api.handler"
  filename         = data.archive_file.lambda.output_path
  source_code_hash = data.archive_file.lambda.output_base64sha256
  memory_size      = 256
  timeout          = 15
  environment {
    variables = merge(local.common_env, {
      QUEUE_URL            = var.queue_url
      QUOTES_BUCKET        = var.quotes_bucket
      SCHEDULER_GROUP      = var.scheduler_group_name
      SCHEDULER_TARGET_ARN = aws_lambda_function.scheduler.arn
      SCHEDULER_ROLE_ARN   = aws_iam_role.scheduler.arn
      LINK_SECRET_PARAM    = aws_ssm_parameter.link_secret.name
      PUBLIC_BASE_URL      = aws_apigatewayv2_api.http.api_endpoint
    })
  }
  depends_on = [aws_cloudwatch_log_group.fn]
}

resource "aws_lambda_function" "scheduler" {
  function_name    = "${var.name}-scheduler"
  role             = aws_iam_role.scheduler_fn.arn
  runtime          = "python3.12"
  architectures    = ["arm64"]
  handler          = "handlers.scheduler.handler"
  filename         = data.archive_file.lambda.output_path
  source_code_hash = data.archive_file.lambda.output_base64sha256
  memory_size      = 128
  timeout          = 15
  environment { variables = local.common_env }
  depends_on = [aws_cloudwatch_log_group.fn]
}

resource "aws_lambda_function" "archiver" {
  function_name    = "${var.name}-archiver"
  role             = aws_iam_role.archiver.arn
  runtime          = "python3.12"
  architectures    = ["arm64"]
  handler          = "handlers.audit_archiver.handler"
  filename         = data.archive_file.lambda.output_path
  source_code_hash = data.archive_file.lambda.output_base64sha256
  memory_size      = 128
  timeout          = 30
  environment { variables = merge(local.common_env, { AUDIT_BUCKET = var.audit_bucket }) }
  depends_on = [aws_cloudwatch_log_group.fn]
}

resource "aws_lambda_event_source_mapping" "audit_stream" {
  event_source_arn                   = var.table_stream_arn
  function_name                      = aws_lambda_function.archiver.arn
  starting_position                  = "LATEST"
  batch_size                         = 50
  maximum_batching_window_in_seconds = 10
  maximum_retry_attempts             = 5
  function_response_types            = ["ReportBatchItemFailures"]
  filter_criteria {
    filter {
      pattern = jsonencode({
        eventName = ["INSERT"]
        dynamodb  = { Keys = { SK = { S = [{ prefix = "AUDIT#" }] } } }
      })
    }
  }
}

# ---------------------------------------------------------------- HTTP API
resource "aws_apigatewayv2_api" "http" {
  name          = "${var.name}-http"
  protocol_type = "HTTP"
  cors_configuration {
    allow_origins = var.cors_origins
    allow_methods = ["GET", "POST", "OPTIONS"]
    allow_headers = ["authorization", "content-type", "idempotency-key"]
    max_age       = 3600
  }
}

resource "aws_apigatewayv2_authorizer" "jwt" {
  api_id           = aws_apigatewayv2_api.http.id
  authorizer_type  = "JWT"
  name             = "cognito"
  identity_sources = ["$request.header.Authorization"]
  jwt_configuration {
    issuer   = "https://${var.user_pool_endpoint}"
    audience = [var.app_client_id]
  }
}

resource "aws_apigatewayv2_integration" "api" {
  api_id                 = aws_apigatewayv2_api.http.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.api.invoke_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = 15000
}

locals {
  auth_routes = [
    "POST /quotes", "GET /quotes", "GET /quotes/{id}", "GET /quotes/{id}/audit",
    "POST /quotes/{id}/confirm", "POST /quotes/{id}/revise", "POST /quotes/{id}/approve",
    "POST /quotes/{id}/request-changes", "POST /quotes/{id}/send", "POST /quotes/{id}/reject",
    "POST /quotes/{id}/cancel", "POST /quotes/{id}/actual-headcount",
  ]
  public_routes = ["GET /public/quotes/{id}", "POST /public/quotes/{id}/accept", "POST /public/quotes/{id}/decline"]
}

resource "aws_apigatewayv2_route" "auth" {
  for_each           = toset(local.auth_routes)
  api_id             = aws_apigatewayv2_api.http.id
  route_key          = each.value
  target             = "integrations/${aws_apigatewayv2_integration.api.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.jwt.id
}

resource "aws_apigatewayv2_route" "public" {
  for_each  = toset(local.public_routes)
  api_id    = aws_apigatewayv2_api.http.id
  route_key = each.value
  target    = "integrations/${aws_apigatewayv2_integration.api.id}"
}

resource "aws_cloudwatch_log_group" "api_access" {
  name              = "/aws/apigateway/${var.name}-http"
  retention_in_days = var.log_retention_days
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.http.id
  name        = "$default"
  auto_deploy = true
  default_route_settings {
    throttling_burst_limit = var.throttle_burst
    throttling_rate_limit  = var.throttle_rate
  }
  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api_access.arn
    format = jsonencode({
      requestId = "$context.requestId", ip = "$context.identity.sourceIp", route = "$context.routeKey",
      status    = "$context.status", latency = "$context.responseLatency", user = "$context.authorizer.claims.email"
    })
  }
}

resource "aws_lambda_permission" "apigw" {
  statement_id  = "AllowHttpApi"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.http.execution_arn}/*/*"
}
