"""Best-effort EventBridge publishing. A failed publish must never fail a state change."""

from __future__ import annotations

import json
import logging
from functools import lru_cache

from .config import settings

log = logging.getLogger(__name__)
SOURCE = "quote-agent"


@lru_cache(maxsize=1)
def _client():
    import boto3

    return boto3.client("events")


def publish(detail_type: str, detail: dict) -> None:
    bus = settings().event_bus_name
    if not bus:
        log.debug("EVENT_BUS_NAME not set, skipping %s", detail_type)
        return
    try:
        _client().put_events(Entries=[{
            "Source": SOURCE,
            "DetailType": detail_type,
            "Detail": json.dumps(detail, default=str),
            "EventBusName": bus,
        }])
    except Exception:  # noqa: BLE001
        log.exception("failed to publish %s", detail_type)
