"""Load and validate the pricing catalog (versioned JSON in S3)."""

from __future__ import annotations

import json
from decimal import Decimal
from functools import lru_cache
from typing import Any

PRICING_MODELS = {"per_employee_month", "per_site_month", "one_time"}


class CatalogError(ValueError):
    pass


def validate_catalog(cat: dict[str, Any]) -> dict[str, Any]:
    """Fail fast on a malformed catalog. Returns the catalog unchanged."""
    for key in ("version", "currency", "tiers", "services", "defaultPackages", "termDiscounts", "approval", "tax"):
        if key not in cat:
            raise CatalogError(f"catalog missing '{key}'")

    tiers = cat["tiers"]
    if not tiers:
        raise CatalogError("catalog has no tiers")
    tier_names = [t["name"] for t in tiers]
    prev_max = 0
    for t in sorted(tiers, key=lambda t: t["minEmployees"]):
        if t["minEmployees"] != prev_max + 1:
            raise CatalogError(f"tier '{t['name']}' leaves a gap or overlap at {t['minEmployees']}")
        if t["maxEmployees"] < t["minEmployees"]:
            raise CatalogError(f"tier '{t['name']}' has max < min")
        prev_max = t["maxEmployees"]

    skus = set()
    for s in cat["services"]:
        sku = s.get("sku")
        if not sku or sku in skus:
            raise CatalogError(f"missing or duplicate sku: {sku!r}")
        skus.add(sku)
        if s.get("pricing") not in PRICING_MODELS:
            raise CatalogError(f"{sku}: unknown pricing model {s.get('pricing')!r}")
        if "tierPrices" in s:
            missing = set(tier_names) - set(s["tierPrices"])
            if missing:
                raise CatalogError(f"{sku}: tierPrices missing {sorted(missing)}")
        elif "price" not in s:
            raise CatalogError(f"{sku}: needs 'price' or 'tierPrices'")
        if not isinstance(s.get("name"), dict) or "en" not in s["name"]:
            raise CatalogError(f"{sku}: name must be a dict with at least 'en'")

    for s in cat["services"]:
        for ref in s.get("requires", []) + s.get("excludes", []):
            if ref not in skus:
                raise CatalogError(f"{s['sku']} references unknown sku {ref}")

    for tier, pkg in cat["defaultPackages"].items():
        if tier not in tier_names:
            raise CatalogError(f"defaultPackages has unknown tier {tier}")
        unknown = set(pkg) - skus
        if unknown:
            raise CatalogError(f"defaultPackages[{tier}] has unknown skus {sorted(unknown)}")

    if "12" not in cat["termDiscounts"]:
        raise CatalogError("termDiscounts must include '12'")
    return cat


def parse_catalog(raw: bytes | str) -> dict[str, Any]:
    # parse floats as Decimal so money math is exact
    return validate_catalog(json.loads(raw, parse_float=Decimal))


@lru_cache(maxsize=4)
def _load_from_s3(bucket: str, key: str, etag: str) -> dict[str, Any]:  # etag busts the cache
    import boto3

    body = boto3.client("s3").get_object(Bucket=bucket, Key=key)["Body"].read()
    return parse_catalog(body)


def load_catalog(bucket: str, key: str) -> dict[str, Any]:
    import boto3

    etag = boto3.client("s3").head_object(Bucket=bucket, Key=key)["ETag"]
    return _load_from_s3(bucket, key, etag)
