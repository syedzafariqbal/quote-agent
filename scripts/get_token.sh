#!/usr/bin/env bash
# Print a Cognito ID token for REST Client / curl.  Usage: ./scripts/get_token.sh user@example.com
set -euo pipefail
EMAIL="${1:?usage: get_token.sh <email>}"
ENV="${ENV:-dev}"
DIR="$(cd "$(dirname "$0")/.." && pwd)/infra/envs/$ENV"
CLIENT_ID="$(cd "$DIR" && terraform output -raw app_client_id)"
read -rsp "Password for $EMAIL: " PASSWORD; echo >&2

aws cognito-idp initiate-auth \
  --auth-flow USER_PASSWORD_AUTH \
  --client-id "$CLIENT_ID" \
  --auth-parameters "USERNAME=$EMAIL,PASSWORD=$PASSWORD" \
  --query 'AuthenticationResult.IdToken' --output text
