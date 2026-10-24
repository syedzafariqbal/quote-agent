"""ECS worker: long-polls SQS and runs the pipeline. Handles Fargate Spot SIGTERM gracefully
(finishes the in-flight message, stops polling; unfinished messages reappear after the
visibility timeout and are retried, up to maxReceiveCount, then go to the DLQ)."""

from __future__ import annotations

import json
import logging
import os
import signal
import sys

import boto3

from quote_core.config import settings
from worker.pipeline import default_deps, handle_message, mark_failed

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                    format='{"level":"%(levelname)s","logger":"%(name)s","msg":%(message)r}', stream=sys.stdout)
log = logging.getLogger("worker")

_running = True


def _stop(signum, _frame):
    global _running
    log.info("signal %s received, finishing current message then exiting", signum)
    _running = False


def main() -> None:
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    cfg = settings()
    if not cfg.queue_url:
        raise SystemExit("QUEUE_URL is not set")
    sqs = boto3.client("sqs")
    deps = default_deps(cfg)
    log.info("worker started, polling %s", cfg.queue_url)

    while _running:
        resp = sqs.receive_message(QueueUrl=cfg.queue_url, MaxNumberOfMessages=1, WaitTimeSeconds=20,
                                   MessageSystemAttributeNames=["ApproximateReceiveCount"])
        for m in resp.get("Messages", []):
            body = json.loads(m["Body"])
            receive_count = int(m.get("Attributes", {}).get("ApproximateReceiveCount", "1"))
            try:
                status = handle_message(body, deps)
                log.info("quote %s -> %s", body.get("quoteId"), status)
                sqs.delete_message(QueueUrl=cfg.queue_url, ReceiptHandle=m["ReceiptHandle"])
            except Exception as exc:  # noqa: BLE001
                log.exception("failed quote %s (attempt %s)", body.get("quoteId"), receive_count)
                if receive_count >= cfg.max_receive_count:
                    mark_failed(body["quoteId"], deps, f"{type(exc).__name__}: {exc}")
                # leave the message; SQS redelivers after the visibility timeout, then DLQ


if __name__ == "__main__":
    main()
