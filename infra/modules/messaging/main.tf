# SQS work queue + DLQ, EventBridge bus, Scheduler group, SNS alert topic.

resource "aws_sqs_queue" "dlq" {
  name                      = "${var.name}-quote-requests-dlq"
  message_retention_seconds = 1209600 # 14 days
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "requests" {
  name                       = "${var.name}-quote-requests"
  visibility_timeout_seconds = var.visibility_timeout_seconds
  receive_wait_time_seconds  = 20
  message_retention_seconds  = 345600 # 4 days
  sqs_managed_sse_enabled    = true
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = var.max_receive_count
  })
}

resource "aws_sqs_queue_redrive_allow_policy" "dlq" {
  queue_url = aws_sqs_queue.dlq.id
  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.requests.arn]
  })
}

# ---------------------------------------------------------------- EventBridge
resource "aws_cloudwatch_event_bus" "quotes" {
  name = "${var.name}-quotes"
}

# Every lifecycle event lands in a log group: free-ish analytics with Logs Insights.
resource "aws_cloudwatch_log_group" "events" {
  name              = "/aws/events/${var.name}-quotes"
  retention_in_days = var.event_log_retention_days
}

data "aws_iam_policy_document" "events_to_logs" {
  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.events.arn}:*"]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com", "delivery.logs.amazonaws.com"]
    }
  }
}

resource "aws_cloudwatch_log_resource_policy" "events" {
  policy_name     = "${var.name}-events-to-logs"
  policy_document = data.aws_iam_policy_document.events_to_logs.json
}

resource "aws_cloudwatch_event_rule" "all_quote_events" {
  name           = "${var.name}-all-quote-events"
  event_bus_name = aws_cloudwatch_event_bus.quotes.name
  event_pattern  = jsonencode({ source = ["quote-agent"] })
}

resource "aws_cloudwatch_event_target" "all_to_logs" {
  rule           = aws_cloudwatch_event_rule.all_quote_events.name
  event_bus_name = aws_cloudwatch_event_bus.quotes.name
  arn            = aws_cloudwatch_log_group.events.arn
}

# Failures page the operator
resource "aws_cloudwatch_event_rule" "failed" {
  name           = "${var.name}-quote-failed"
  event_bus_name = aws_cloudwatch_event_bus.quotes.name
  event_pattern  = jsonencode({ source = ["quote-agent"], "detail-type" = ["QuoteFailed"] })
}

resource "aws_cloudwatch_event_target" "failed_to_sns" {
  rule           = aws_cloudwatch_event_rule.failed.name
  event_bus_name = aws_cloudwatch_event_bus.quotes.name
  arn            = aws_sns_topic.alerts.arn
}

# ---------------------------------------------------------------- Scheduler
resource "aws_scheduler_schedule_group" "quotes" {
  name = "${var.name}-quotes"
}

# ---------------------------------------------------------------- Alerts
# Not encrypted with alias/aws/sns on purpose: that AWS-managed key does not let EventBridge,
# CloudWatch or Budgets publish. Use a customer-managed KMS key (~1 USD/month) if required.
resource "aws_sns_topic" "alerts" {
  name = "${var.name}-alerts"
}

data "aws_iam_policy_document" "alerts" {
  statement {
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alerts.arn]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com", "cloudwatch.amazonaws.com", "budgets.amazonaws.com"]
    }
  }
}

resource "aws_sns_topic_policy" "alerts" {
  arn    = aws_sns_topic.alerts.arn
  policy = data.aws_iam_policy_document.alerts.json
}

resource "aws_sns_topic_subscription" "alert_email" {
  count     = var.alert_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}
