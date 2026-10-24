#!/usr/bin/env bash
# Write .env (git-ignored) from Terraform outputs so VS Code can run the worker / evals locally
# against the dev stack. Your AWS profile supplies credentials (export AWS_PROFILE first).
set -euo pipefail
ENV="${1:-dev}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/infra/envs/$ENV"
out() { terraform output -raw "$1"; }
tfvar() { grep -E "^\s*$1\s*=" terraform.tfvars 2>/dev/null | head -1 | sed -E 's/.*=\s*"?([^"#]*)"?.*/\1/' | xargs; }

cat > "$ROOT/.env" <<EOF
AWS_REGION=eu-central-1
AWS_PROFILE=${AWS_PROFILE:-default}
TABLE_NAME=$(out table_name)
QUEUE_URL=$(out queue_url)
QUOTES_BUCKET=$(out quotes_bucket)
CATALOG_BUCKET=$(out catalog_bucket)
CATALOG_KEY=catalog/current.json
ESTIMATOR_MODEL_ID=$(tfvar estimator_model_id)
WRITER_MODEL_ID=$(tfvar writer_model_id)
CONFIDENCE_THRESHOLD=0.7
EVENT_BUS_NAME=
SENDER_EMAIL=
EOF
echo "wrote $ROOT/.env"
