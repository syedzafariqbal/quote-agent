variable "name" {
  type = string
}

variable "project" {
  type = string
}

variable "account_id" {
  type = string
}

variable "monthly_budget_usd" {
  type    = number
  default = 30
}

variable "alert_email" {
  type = string
}

variable "alerts_topic_arn" {
  type = string
}

variable "enable_budget_action" {
  type    = bool
  default = true
}

variable "worker_task_role_name" {
  type = string
}

variable "queue_name" {
  type = string
}

variable "dlq_name" {
  type = string
}

variable "api_function_name" {
  type = string
}

variable "cluster_name" {
  type = string
}

variable "service_name" {
  type = string
}

variable "events_log_group" {
  type = string
}

variable "budget_filter_by_tag" {
  type    = bool
  default = false
}
