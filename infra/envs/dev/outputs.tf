output "api_endpoint" {
  value = module.api.api_endpoint
}

output "user_pool_id" {
  value = module.auth.user_pool_id
}

output "app_client_id" {
  value = module.auth.app_client_id
}

output "ecr_repository_url" {
  value = module.worker.ecr_repository_url
}

output "ecs_cluster" {
  value = module.worker.cluster_name
}

output "ecs_service" {
  value = module.worker.service_name
}

output "queue_url" {
  value = module.messaging.queue_url
}

output "dlq_url" {
  value = module.messaging.dlq_url
}

output "table_name" {
  value = module.data.table_name
}

output "quotes_bucket" {
  value = module.data.quotes_bucket
}

output "catalog_bucket" {
  value = module.data.catalog_bucket
}

output "worker_log_group" {
  value = module.worker.log_group
}

output "dashboard" {
  value = module.observability.dashboard_name
}
