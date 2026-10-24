data "aws_caller_identity" "current" {}

locals {
  name       = "${var.project}-${var.env}"
  account_id = data.aws_caller_identity.current.account_id
  repo_root  = abspath("${path.root}/../../..")
}

module "network" {
  source = "../../modules/network"
  name   = local.name
  cidr   = var.vpc_cidr
}

module "data" {
  source              = "../../modules/data"
  name                = local.name
  account_id          = local.account_id
  catalog_file        = "${local.repo_root}/catalog/${var.catalog_file}"
  force_destroy       = var.dev_mode
  enable_object_lock  = var.enable_object_lock
  enable_pitr         = !var.dev_mode
  deletion_protection = !var.dev_mode
}

module "messaging" {
  source            = "../../modules/messaging"
  name              = local.name
  alert_email       = var.alert_email
  max_receive_count = var.max_receive_count
}

module "auth" {
  source              = "../../modules/auth"
  name                = local.name
  deletion_protection = !var.dev_mode
  allow_password_auth = true
}

module "api" {
  source               = "../../modules/api"
  name                 = local.name
  account_id           = local.account_id
  source_dir           = "${local.repo_root}/app/src"
  table_name           = module.data.table_name
  table_arn            = module.data.table_arn
  table_stream_arn     = module.data.table_stream_arn
  queue_url            = module.messaging.queue_url
  queue_arn            = module.messaging.queue_arn
  max_receive_count    = var.max_receive_count
  quotes_bucket        = module.data.quotes_bucket
  quotes_bucket_arn    = module.data.quotes_bucket_arn
  audit_bucket         = module.data.audit_bucket
  audit_bucket_arn     = module.data.audit_bucket_arn
  event_bus_name       = module.messaging.event_bus_name
  event_bus_arn        = module.messaging.event_bus_arn
  scheduler_group_name = module.messaging.scheduler_group_name
  user_pool_endpoint   = module.auth.user_pool_endpoint
  app_client_id        = module.auth.app_client_id
  sender_email         = var.sender_email
  approver_email       = var.approver_email
  cors_origins         = var.cors_origins
}

module "worker" {
  source             = "../../modules/worker"
  name               = local.name
  account_id         = local.account_id
  image_tag          = var.worker_image_tag
  subnet_ids         = module.network.subnet_ids
  security_group_id  = module.network.worker_security_group_id
  queue_arn          = module.messaging.queue_arn
  queue_name         = module.messaging.queue_name
  table_arn          = module.data.table_arn
  catalog_bucket_arn = module.data.catalog_bucket_arn
  quotes_bucket_arn  = module.data.quotes_bucket_arn
  event_bus_arn      = module.messaging.event_bus_arn
  use_spot           = true
  min_tasks          = var.worker_min_tasks
  max_tasks          = var.worker_max_tasks
  force_delete       = var.dev_mode
  environment = {
    TABLE_NAME           = module.data.table_name
    QUEUE_URL            = module.messaging.queue_url
    QUOTES_BUCKET        = module.data.quotes_bucket
    CATALOG_BUCKET       = module.data.catalog_bucket
    CATALOG_KEY          = module.data.catalog_key
    EVENT_BUS_NAME       = module.messaging.event_bus_name
    ESTIMATOR_MODEL_ID   = var.estimator_model_id
    WRITER_MODEL_ID      = var.writer_model_id
    CONFIDENCE_THRESHOLD = tostring(var.confidence_threshold)
    MAX_RECEIVE_COUNT    = tostring(var.max_receive_count)
    SENDER_EMAIL         = var.sender_email
    APPROVER_EMAIL       = var.approver_email
    LOG_LEVEL            = "INFO"
  }
}

module "observability" {
  source                = "../../modules/observability"
  name                  = local.name
  project               = var.project
  account_id            = local.account_id
  monthly_budget_usd    = var.monthly_budget_usd
  alert_email           = var.alert_email
  alerts_topic_arn      = module.messaging.alerts_topic_arn
  enable_budget_action  = var.enable_budget_action
  worker_task_role_name = module.worker.task_role_name
  queue_name            = module.messaging.queue_name
  dlq_name              = module.messaging.dlq_name
  api_function_name     = module.api.api_function_name
  cluster_name          = module.worker.cluster_name
  service_name          = module.worker.service_name
  events_log_group      = module.messaging.events_log_group
}
