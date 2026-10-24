"""JSON helpers: DynamoDB returns Decimals, API responses need plain JSON."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from decimal import Decimal
from typing import Any


def _default(o: Any):
    if isinstance(o, Decimal):
        return int(o) if o == o.to_integral_value() and o.as_tuple().exponent >= 0 else str(o)
    if isinstance(o, set):
        return sorted(o)
    if is_dataclass(o):
        return asdict(o)
    raise TypeError(f"not serialisable: {type(o)}")


def dumps(obj: Any) -> str:
    return json.dumps(obj, default=_default, ensure_ascii=False)


def to_dynamo(obj: Any) -> Any:
    """Convert floats to Decimal recursively (DynamoDB rejects floats)."""
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: to_dynamo(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [to_dynamo(v) for v in obj]
    if is_dataclass(obj):
        return to_dynamo(asdict(obj))
    return obj
