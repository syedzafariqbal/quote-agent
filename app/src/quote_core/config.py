"""Runtime configuration from environment variables (set by Terraform)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Settings:
    table_name: str
    queue_url: str
    quotes_bucket: str
    catalog_bucket: str
    catalog_key: str
    event_bus_name: str
    scheduler_group: str
    scheduler_target_arn: str
    scheduler_role_arn: str
    sender_email: str
    approver_email: str
    link_secret_param: str
    public_base_url: str
    estimator_model_id: str
    writer_model_id: str
    confidence_threshold: float
    max_receive_count: int


@lru_cache(maxsize=1)
def settings() -> Settings:
    env = os.environ.get
    return Settings(
        table_name=env("TABLE_NAME", "quote-agent-local"),
        queue_url=env("QUEUE_URL", ""),
        quotes_bucket=env("QUOTES_BUCKET", ""),
        catalog_bucket=env("CATALOG_BUCKET", ""),
        catalog_key=env("CATALOG_KEY", "catalog/current.json"),
        event_bus_name=env("EVENT_BUS_NAME", ""),
        scheduler_group=env("SCHEDULER_GROUP", ""),
        scheduler_target_arn=env("SCHEDULER_TARGET_ARN", ""),
        scheduler_role_arn=env("SCHEDULER_ROLE_ARN", ""),
        sender_email=env("SENDER_EMAIL", ""),
        approver_email=env("APPROVER_EMAIL", ""),
        link_secret_param=env("LINK_SECRET_PARAM", ""),
        public_base_url=env("PUBLIC_BASE_URL", "").rstrip("/"),
        estimator_model_id=env("ESTIMATOR_MODEL_ID", ""),
        writer_model_id=env("WRITER_MODEL_ID", ""),
        confidence_threshold=float(env("CONFIDENCE_THRESHOLD", "0.7")),
        max_receive_count=int(env("MAX_RECEIVE_COUNT", "3")),
    )
