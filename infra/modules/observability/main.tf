# Budget guardrails, alarms and a small (free-tier) dashboard.

data "aws_region" "current" {}

# ---------------------------------------------------------------- budget
resource "aws_budgets_budget" "monthly" {
  name         = "${var.name}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  # Whole-account budget by default (use a dedicated account for this project). To scope it to
  # this project only, activate the "Project" cost allocation tag in Billing, then set budget_filter_by_tag.
  dynamic "cost_filter" {
    for_each = var.budget_filter_by_tag ? [1] : []
    content {
      name   = "TagKeyValue"
      values = [format("user:Project$%s", var.project)]
    }
  }

  dynamic "notification" {
    for_each = [50, 80, 100]
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      notification_type          = "ACTUAL"
      subscriber_email_addresses = var.alert_email == "" ? [] : [var.alert_email]
      subscriber_sns_topic_arns  = [var.alerts_topic_arn]
    }
  }
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = var.alert_email == "" ? [] : [var.alert_email]
    subscriber_sns_topic_arns  = [var.alerts_topic_arn]
  }
}

# Hard stop for the LLM: at 100% of budget, attach a deny-Bedrock policy to the worker task role.
# Quotes then fail into FAILED/DLQ instead of running up a bill. Detach the policy to resume.
resource "aws_iam_policy" "deny_bedrock" {
  count       = var.enable_budget_action ? 1 : 0
  name        = "${var.name}-deny-bedrock"
  description = "Attached automatically by AWS Budgets when the monthly budget is exceeded"
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Deny", Action = "bedrock:*", Resource = "*" }]
  })
}

data "aws_iam_policy_document" "budgets_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["budgets.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "budget_action" {
  count              = var.enable_budget_action ? 1 : 0
  name               = "${var.name}-budget-action"
  assume_role_policy = data.aws_iam_policy_document.budgets_trust.json
}

resource "aws_iam_role_policy" "budget_action" {
  count = var.enable_budget_action ? 1 : 0
  name  = "${var.name}-budget-action"
  role  = aws_iam_role.budget_action[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = ["iam:AttachRolePolicy", "iam:DetachRolePolicy"]
      Resource  = "arn:aws:iam::${var.account_id}:role/${var.worker_task_role_name}"
      Condition = { ArnEquals = { "iam:PolicyARN" = aws_iam_policy.deny_bedrock[0].arn } }
    }]
  })
}

resource "aws_budgets_budget_action" "deny_bedrock" {
  count              = var.enable_budget_action ? 1 : 0
  budget_name        = aws_budgets_budget.monthly.name
  action_type        = "APPLY_IAM_POLICY"
  approval_model     = "AUTOMATIC"
  notification_type  = "ACTUAL"
  execution_role_arn = aws_iam_role.budget_action[0].arn

  action_threshold {
    action_threshold_type  = "PERCENTAGE"
    action_threshold_value = 100
  }

  definition {
    iam_action_definition {
      policy_arn = aws_iam_policy.deny_bedrock[0].arn
      roles      = [var.worker_task_role_name]
    }
  }

  subscriber {
    address           = var.alert_email
    subscription_type = "EMAIL"
  }
}

# ---------------------------------------------------------------- alarms
resource "aws_cloudwatch_metric_alarm" "dlq" {
  alarm_name          = "${var.name}-dlq-not-empty"
  alarm_description   = "Quote requests failed repeatedly and landed in the DLQ"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = var.dlq_name }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [var.alerts_topic_arn]
}

resource "aws_cloudwatch_metric_alarm" "api_errors" {
  alarm_name          = "${var.name}-api-lambda-errors"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = var.api_function_name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 5
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [var.alerts_topic_arn]
}

# ---------------------------------------------------------------- dashboard (first 3 are free)
resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = "${var.name}-quotes"
  dashboard_body = jsonencode({
    widgets = [
      {
        type = "metric", x = 0, y = 0, width = 12, height = 6
        properties = {
          title = "Queue backlog", region = data.aws_region.current.region, stat = "Maximum", period = 60
          metrics = [
            ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", var.queue_name],
            [".", "ApproximateNumberOfMessagesNotVisible", ".", "."],
            [".", "ApproximateNumberOfMessagesVisible", ".", var.dlq_name, { label = "DLQ" }],
          ]
        }
      },
      {
        type = "metric", x = 12, y = 0, width = 12, height = 6
        properties = {
          title = "Worker CPU / memory", region = data.aws_region.current.region, stat = "Average", period = 60
          metrics = [
            ["AWS/ECS", "CPUUtilization", "ClusterName", var.cluster_name, "ServiceName", var.service_name],
            [".", "MemoryUtilization", ".", ".", ".", "."],
          ]
        }
      },
      {
        type = "metric", x = 0, y = 6, width = 12, height = 6
        properties = {
          title = "API Lambda", region = data.aws_region.current.region, stat = "Sum", period = 300
          metrics = [
            ["AWS/Lambda", "Invocations", "FunctionName", var.api_function_name],
            [".", "Errors", ".", "."],
          ]
        }
      },
      {
        type = "log", x = 12, y = 6, width = 12, height = 6
        properties = {
          title = "Lifecycle events (last 24h)", region = data.aws_region.current.region
          query = "SOURCE '${var.events_log_group}' | stats count() by `detail-type` | sort count() desc"
          view  = "table"
        }
      },
    ]
  })
}
