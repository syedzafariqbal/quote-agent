variable "name" {
  type = string
}

variable "account_id" {
  type = string
}

variable "catalog_file" {
  description = "Path to the pricing catalog JSON uploaded to S3"
  type        = string
}

variable "force_destroy" {
  description = "Allow terraform destroy to delete non-empty buckets (dev only)"
  type        = bool
  default     = false
}

variable "enable_object_lock" {
  description = "S3 Object Lock on the audit bucket. Locked objects block bucket deletion until retention ends."
  type        = bool
  default     = false
}

variable "audit_retention_days" {
  type    = number
  default = 365
}

variable "pdf_retention_days" {
  type    = number
  default = 730
}

variable "enable_pitr" {
  type    = bool
  default = true
}

variable "deletion_protection" {
  type    = bool
  default = false
}
