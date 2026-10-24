#!/usr/bin/env bash
# Create a Cognito user with a permanent password and add it to a group.
# Usage: ./scripts/create_user.sh rep@example.com SalesRep
#        groups: SalesRep | Manager | Finance | Admin
set -euo pipefail
EMAIL="${1:?usage: create_user.sh <email> <group>}"
GROUP="${2:?usage: create_user.sh <email> <group>}"
ENV="${ENV:-dev}"
DIR="$(cd "$(dirname "$0")/.." && pwd)/infra/envs/$ENV"
POOL="$(cd "$DIR" && terraform output -raw user_pool_id)"
read -rsp "Password for $EMAIL (12+ chars, upper, lower, digit): " PASSWORD; echo

aws cognito-idp admin-create-user --user-pool-id "$POOL" --username "$EMAIL" \
  --user-attributes Name=email,Value="$EMAIL" Name=email_verified,Value=true \
  --message-action SUPPRESS >/dev/null
aws cognito-idp admin-set-user-password --user-pool-id "$POOL" --username "$EMAIL" \
  --password "$PASSWORD" --permanent
aws cognito-idp admin-add-user-to-group --user-pool-id "$POOL" --username "$EMAIL" --group-name "$GROUP"
echo "created $EMAIL in group $GROUP"
