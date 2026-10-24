variable "name" {
  type = string
}

variable "account_id" {
  type = string
}

variable "source_dir" {
  description = "app/src directory packaged into the Lambda zip"
  type        = string
}

variable "table_name" {
  type = string
}

variable "table_arn" {
  type = string
}

variable "table_stream_arn" {
  type = string
}

variable "queue_url" {
  type = string
}

variable "queue_arn" {
  type = string
}

variable "max_receive_count" {
  type    = number
  default = 3
}

variable "quotes_bucket" {
  type = string
}

variable "quotes_bucket_arn" {
  type = string
}

variable "audit_bucket" {
  type = string
}

variable "audit_bucket_arn" {
  type = string
}

variable "event_bus_name" {
  type = string
}

variable "event_bus_arn" {
  type = string
}

variable "scheduler_group_name" {
  type = string
}

variable "user_pool_endpoint" {
  type = string
}

variable "app_client_id" {
  type = string
}

variable "sender_email" {
  type    = string
  default = ""
}

variable "approver_email" {
  type    = string
  default = ""
}

variable "cors_origins" {
  type    = list(string)
  default = ["http://localhost:5173"]
}

variable "throttle_rate" {
  type    = number
  default = 10
}

variable "throttle_burst" {
  type    = number
  default = 20
}

variable "log_retention_days" {
  type    = number
  default = 14
}
