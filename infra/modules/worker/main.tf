# ECS Fargate Spot worker that scales 0 -> N on queue backlog.

data "aws_region" "current" {}

locals {
  region = data.aws_region.current.region
  image  = "${aws_ecr_repository.worker.repository_url}:${var.image_tag}"
}

# ---------------------------------------------------------------- ECR
resource "aws_ecr_repository" "worker" {
  name                 = "${var.name}-worker"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = var.force_delete
  image_scanning_configuration { scan_on_push = true }
  encryption_configuration { encryption_type = "AES256" }
}

resource "aws_ecr_lifecycle_policy" "worker" {
  repository = aws_ecr_repository.worker.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "keep last 10 images"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 10 }
      action       = { type = "expire" }
    }]
  })
}

# ---------------------------------------------------------------- cluster
resource "aws_ecs_cluster" "this" {
  name = var.name
  setting {
    name  = "containerInsights"
    value = var.container_insights ? "enabled" : "disabled" # costs extra; off by default
  }
}

resource "aws_ecs_cluster_capacity_providers" "this" {
  cluster_name       = aws_ecs_cluster.this.name
  capacity_providers = ["FARGATE", "FARGATE_SPOT"]
  default_capacity_provider_strategy {
    capacity_provider = var.use_spot ? "FARGATE_SPOT" : "FARGATE"
    weight            = 1
  }
}

resource "aws_cloudwatch_log_group" "worker" {
  name              = "/ecs/${var.name}-worker"
  retention_in_days = var.log_retention_days
}

# ---------------------------------------------------------------- IAM
data "aws_iam_policy_document" "ecs_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "execution" {
  name               = "${var.name}-worker-exec"
  assume_role_policy = data.aws_iam_policy_document.ecs_trust.json
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role" "task" {
  name               = "${var.name}-worker-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_trust.json
}

data "aws_iam_policy_document" "task" {
  statement {
    sid       = "Queue"
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:ChangeMessageVisibility", "sqs:GetQueueAttributes"]
    resources = [var.queue_arn]
  }
  statement {
    sid = "Table"
    actions = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:Query",
    "dynamodb:ConditionCheckItem"]
    resources = [var.table_arn, "${var.table_arn}/index/*"]
  }
  statement {
    sid       = "Catalog"
    actions   = ["s3:GetObject"]
    resources = ["${var.catalog_bucket_arn}/catalog/*"]
  }
  statement {
    sid       = "Pdfs"
    actions   = ["s3:PutObject"]
    resources = ["${var.quotes_bucket_arn}/quotes/*"]
  }
  statement {
    sid       = "Events"
    actions   = ["events:PutEvents"]
    resources = [var.event_bus_arn]
  }
  statement {
    sid     = "Bedrock"
    actions = ["bedrock:InvokeModel"]
    resources = [
      "arn:aws:bedrock:*::foundation-model/*",
      "arn:aws:bedrock:*:${var.account_id}:inference-profile/*",
    ]
  }
  statement {
    sid       = "Location"
    actions   = ["geo-places:SearchText", "geo-routes:CalculateRoutes"]
    resources = ["arn:aws:geo-places:${local.region}::provider/default", "arn:aws:geo-routes:${local.region}::provider/default"]
  }
  statement {
    sid       = "Email"
    actions   = ["ses:SendEmail"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "task" {
  name   = "${var.name}-worker-task"
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.task.json
}

# ---------------------------------------------------------------- task + service
resource "aws_ecs_task_definition" "worker" {
  family                   = "${var.name}-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.cpu
  memory                   = var.memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  volume {
    name = "tmp" # writable scratch space (fontconfig cache); root filesystem stays read-only
  }

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = var.cpu_architecture
  }

  container_definitions = jsonencode([{
    name                   = "worker"
    image                  = local.image
    essential              = true
    readonlyRootFilesystem = true
    stopTimeout            = 110 # Spot gives a 2-minute warning; finish the in-flight quote
    environment            = [for k, v in var.environment : { name = k, value = v }]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.worker.name
        awslogs-region        = local.region
        awslogs-stream-prefix = "worker"
      }
    }
  }])
}

resource "aws_ecs_service" "worker" {
  name            = "${var.name}-worker"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.worker.arn
  desired_count   = 0

  capacity_provider_strategy {
    capacity_provider = var.use_spot ? "FARGATE_SPOT" : "FARGATE"
    weight            = 1
  }

  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = [var.security_group_id]
    assign_public_ip = true # no NAT gateway; SG blocks all inbound
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  lifecycle { ignore_changes = [desired_count] }
  depends_on = [aws_ecs_cluster_capacity_providers.this]
}

# ---------------------------------------------------------------- scale 0..N on backlog
resource "aws_appautoscaling_target" "worker" {
  service_namespace  = "ecs"
  resource_id        = "service/${aws_ecs_cluster.this.name}/${aws_ecs_service.worker.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  min_capacity       = var.min_tasks
  max_capacity       = var.max_tasks
}

resource "aws_appautoscaling_policy" "scale_out" {
  name               = "${var.name}-worker-scale-out"
  service_namespace  = aws_appautoscaling_target.worker.service_namespace
  resource_id        = aws_appautoscaling_target.worker.resource_id
  scalable_dimension = aws_appautoscaling_target.worker.scalable_dimension
  policy_type        = "StepScaling"
  step_scaling_policy_configuration {
    adjustment_type         = "ExactCapacity"
    cooldown                = 60
    metric_aggregation_type = "Maximum"
    step_adjustment {
      metric_interval_lower_bound = 0
      metric_interval_upper_bound = 20
      scaling_adjustment          = 1
    }
    step_adjustment {
      metric_interval_lower_bound = 20
      scaling_adjustment          = var.max_tasks
    }
  }
}

resource "aws_appautoscaling_policy" "scale_in" {
  name               = "${var.name}-worker-scale-in"
  service_namespace  = aws_appautoscaling_target.worker.service_namespace
  resource_id        = aws_appautoscaling_target.worker.resource_id
  scalable_dimension = aws_appautoscaling_target.worker.scalable_dimension
  policy_type        = "StepScaling"
  step_scaling_policy_configuration {
    adjustment_type         = "ExactCapacity"
    cooldown                = 120
    metric_aggregation_type = "Maximum"
    step_adjustment {
      metric_interval_upper_bound = 0
      scaling_adjustment          = var.min_tasks
    }
  }
}

# backlog = visible + in-flight messages (so we never scale in while a quote is being built)
locals {
  backlog_queries = [
    {
      id     = "visible", return_data = false,
      metric = { metric_name = "ApproximateNumberOfMessagesVisible", stat = "Maximum" }
    },
    {
      id     = "inflight", return_data = false,
      metric = { metric_name = "ApproximateNumberOfMessagesNotVisible", stat = "Maximum" }
    },
  ]
}

resource "aws_cloudwatch_metric_alarm" "backlog_high" {
  alarm_name          = "${var.name}-worker-backlog"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  threshold           = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_appautoscaling_policy.scale_out.arn]

  dynamic "metric_query" {
    for_each = local.backlog_queries
    content {
      id          = metric_query.value.id
      return_data = metric_query.value.return_data
      metric {
        namespace   = "AWS/SQS"
        metric_name = metric_query.value.metric.metric_name
        period      = 60
        stat        = metric_query.value.metric.stat
        dimensions  = { QueueName = var.queue_name }
      }
    }
  }
  metric_query {
    id          = "backlog"
    expression  = "visible + inflight"
    label       = "Backlog"
    return_data = true
  }
}

resource "aws_cloudwatch_metric_alarm" "backlog_empty" {
  alarm_name          = "${var.name}-worker-idle"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = var.scale_in_after_minutes
  threshold           = 1
  treat_missing_data  = "breaching"
  alarm_actions       = [aws_appautoscaling_policy.scale_in.arn]

  dynamic "metric_query" {
    for_each = local.backlog_queries
    content {
      id          = metric_query.value.id
      return_data = metric_query.value.return_data
      metric {
        namespace   = "AWS/SQS"
        metric_name = metric_query.value.metric.metric_name
        period      = 60
        stat        = metric_query.value.metric.stat
        dimensions  = { QueueName = var.queue_name }
      }
    }
  }
  metric_query {
    id          = "backlog"
    expression  = "visible + inflight"
    label       = "Backlog"
    return_data = true
  }
}
