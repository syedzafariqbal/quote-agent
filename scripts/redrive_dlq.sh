#!/usr/bin/env bash
# Move messages from the DLQ back to the work queue after you fixed the cause.
set -euo pipefail
ENV="${ENV:-dev}"
DIR="$(cd "$(dirname "$0")/.." && pwd)/infra/envs/$ENV"
DLQ_URL="$(cd "$DIR" && terraform output -raw dlq_url)"
DLQ_ARN="$(aws sqs get-queue-attributes --queue-url "$DLQ_URL" --attribute-names QueueArn --query Attributes.QueueArn --output text)"
echo "messages in DLQ: $(aws sqs get-queue-attributes --queue-url "$DLQ_URL" --attribute-names ApproximateNumberOfMessages --query Attributes.ApproximateNumberOfMessages --output text)"
aws sqs start-message-move-task --source-arn "$DLQ_ARN"
echo "redrive started (note: quotes marked FAILED must first be revised/retried via POST /quotes/{id}/revise)"
