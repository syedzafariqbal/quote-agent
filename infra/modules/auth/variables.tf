variable "name" {
  type = string
}

variable "deletion_protection" {
  type    = bool
  default = false
}

variable "allow_password_auth" {
  type    = bool
  default = true
}
