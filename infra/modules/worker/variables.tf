variable "name" {
  type = string
}

variable "account_id" {
  type = string
}

variable "image_tag" {
  description = "Worker image tag in ECR (the git SHA, set by CI)"
  type        = string
}

variable "subnet_ids" {
  type = list(string)
}

variable "security_group_id" {
  type = string
}

variable "queue_arn" {
  type = string
}

variable "queue_name" {
  type = string
}

variable "table_arn" {
  type = string
}

variable "catalog_bucket_arn" {
  type = string
}

variable "quotes_bucket_arn" {
  type = string
}

variable "event_bus_arn" {
  type = string
}

variable "environment" {
  description = "Plain environment variables for the container"
  type        = map(string)
}

variable "cpu" {
  type    = number
  default = 512
}

variable "memory" {
  type    = number
  default = 1024
}

variable "cpu_architecture" {
  description = "X86_64 or ARM64 (must match the image you build)"
  type        = string
  default     = "X86_64"
}

variable "use_spot" {
  type    = bool
  default = true
}

variable "min_tasks" {
  description = "0 = scale to zero (cheapest; first quote after idle waits for a cold start)"
  type        = number
  default     = 0
}

variable "max_tasks" {
  type    = number
  default = 3
}

variable "scale_in_after_minutes" {
  type    = number
  default = 10
}

variable "container_insights" {
  type    = bool
  default = false
}

variable "force_delete" {
  type    = bool
  default = false
}

variable "log_retention_days" {
  type    = number
  default = 14
}
