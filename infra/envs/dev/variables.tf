variable "project" {
  type    = string
  default = "quote-agent"
}

variable "env" {
  type    = string
  default = "dev"
}

variable "region" {
  type    = string
  default = "eu-north-1"
}

variable "dev_mode" {
  description = "true: buckets/ECR/tables can be destroyed freely, no PITR. Set false for prod."
  type        = bool
  default     = true
}

variable "vpc_cidr" {
  type    = string
  default = "10.40.0.0/16"
}

variable "catalog_file" {
  description = "File name inside /catalog uploaded as the current price list"
  type        = string
  default     = "catalog.v2026-10.json"
}

variable "worker_image_tag" {
  description = "ECR tag of the worker image (git SHA). CI sets this; 'bootstrap' before the first push."
  type        = string
  default     = "bootstrap"
}

variable "estimator_model_id" {
  description = "Bedrock model or inference profile for headcount reasoning (check the Bedrock console for IDs enabled in your region)"
  type        = string
}

variable "writer_model_id" {
  description = "Smaller/cheaper Bedrock model for the PDF summary text. Empty = template text only."
  type        = string
  default     = ""
}

variable "confidence_threshold" {
  type    = number
  default = 0.7
}

variable "max_receive_count" {
  type    = number
  default = 3
}

variable "alert_email" {
  description = "Receives budget, DLQ and failure alerts"
  type        = string
}

variable "sender_email" {
  description = "Verified SES identity used as From address. Empty disables email."
  type        = string
  default     = ""
}

variable "approver_email" {
  description = "Where approval requests are emailed"
  type        = string
  default     = ""
}

variable "monthly_budget_usd" {
  type    = number
  default = 30
}

variable "enable_budget_action" {
  description = "Auto-deny Bedrock for the worker at 100% of budget"
  type        = bool
  default     = true
}

variable "enable_object_lock" {
  description = "S3 Object Lock on the audit bucket (keep false in dev: locked objects block destroy)"
  type        = bool
  default     = false
}

variable "worker_min_tasks" {
  type    = number
  default = 0
}

variable "worker_max_tasks" {
  type    = number
  default = 3
}

variable "cors_origins" {
  type    = list(string)
  default = ["http://localhost:5173"]
}
