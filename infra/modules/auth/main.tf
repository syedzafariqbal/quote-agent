# Cognito user pool for staff. Groups map to lifecycle roles.

resource "aws_cognito_user_pool" "this" {
  name                     = "${var.name}-users"
  deletion_protection      = var.deletion_protection ? "ACTIVE" : "INACTIVE"
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]
  mfa_configuration        = "OPTIONAL"

  software_token_mfa_configuration { enabled = true }

  admin_create_user_config { allow_admin_create_user_only = true }

  password_policy {
    minimum_length                   = 12
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = false
    temporary_password_validity_days = 7
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }
}

resource "aws_cognito_user_pool_client" "app" {
  name                          = "${var.name}-app"
  user_pool_id                  = aws_cognito_user_pool.this.id
  generate_secret               = false
  prevent_user_existence_errors = "ENABLED"
  access_token_validity         = 60
  id_token_validity             = 60
  refresh_token_validity        = 30
  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "days"
  }
  # USER_PASSWORD_AUTH lets you fetch tokens from the CLI / REST Client while there is no UI.
  # Remove it once a hosted UI or SRP-based frontend exists.
  explicit_auth_flows = compact([
    "ALLOW_USER_SRP_AUTH",
    "ALLOW_REFRESH_TOKEN_AUTH",
    var.allow_password_auth ? "ALLOW_USER_PASSWORD_AUTH" : "",
  ])
}

resource "aws_cognito_user_group" "groups" {
  for_each     = toset(["SalesRep", "Manager", "Finance", "Admin"])
  name         = each.value
  user_pool_id = aws_cognito_user_pool.this.id
  description  = "Quote agent role: ${each.value}"
}
