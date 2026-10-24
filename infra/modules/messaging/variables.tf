variable "name" {
  type = string
}

variable "visibility_timeout_seconds" {
  description = "Must exceed the longest worker run (research + PDF)"
  type        = number
  default     = 600
}

variable "max_receive_count" {
  type    = number
  default = 3
}

variable "event_log_retention_days" {
  type    = number
  default = 90
}

variable "alert_email" {
  type    = string
  default = ""
}
