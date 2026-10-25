variable "project" {
  type    = string
  default = "quote-agent"
}

variable "region" {
  type    = string
  default = "eu-north-1"
}

variable "github_owner" {
  description = "GitHub user or organisation that owns the repository"
  type        = string
}

variable "github_repo" {
  description = "Repository name (without owner)"
  type        = string
  default     = "quote-agent"
}

variable "github_owner_id" {
  description = "Numeric owner ID. Set it (with github_repo_id) when GitHub sends the immutable sub format repo:owner@id/repo@id:..."
  type        = string
  default     = ""
}

variable "github_repo_id" {
  description = "Numeric repository ID (https://api.github.com/repos/<owner>/<repo> -> id)"
  type        = string
  default     = ""
}

variable "create_github_oidc_provider" {
  description = "Set false if the account already has the token.actions.githubusercontent.com provider"
  type        = bool
  default     = true
}
