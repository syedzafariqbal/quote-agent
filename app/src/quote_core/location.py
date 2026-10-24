"""Amazon Location Service (Places v2 + Routes v2). No resources to create: the
APIs are called directly and authorised by IAM (geo-places:SearchText, geo-routes:CalculateRoutes).

Location does not tell us headcount. It disambiguates the client, gives country and
position, and the road distance drives the travel-fee line item.
"""

from __future__ import annotations

import logging
import math
from decimal import Decimal
from typing import Any

log = logging.getLogger(__name__)

GERMANY_CENTRE = [10.45, 51.16]  # [lon, lat]


def resolve_company(name: str, country_hint: str | None = None, client=None) -> dict[str, Any] | None:
    """Return the best place match plus an `ambiguous` flag, or None if nothing found / API fails."""
    try:
        if client is None:
            import boto3

            client = boto3.client("geo-places")
        params: dict[str, Any] = {"QueryText": name, "MaxResults": 5, "BiasPosition": GERMANY_CENTRE,
                                  "Language": "en"}
        if country_hint:
            params["Filter"] = {"IncludeCountries": [country_hint.upper()]}
        items = client.search_text(**params).get("ResultItems", [])
    except Exception:  # noqa: BLE001 - location is a hint, never a hard dependency
        log.exception("place search failed for %r", name)
        return None
    if not items:
        return None

    def to_match(it: dict) -> dict[str, Any]:
        addr = it.get("Address", {})
        return {
            "title": it.get("Title", ""),
            "address": addr.get("Label", ""),
            "country": (addr.get("Country") or {}).get("Code2", ""),
            "locality": addr.get("Locality", ""),
            "position": [Decimal(str(p)) for p in it.get("Position", [])],
            "categories": [c.get("Name", "") for c in it.get("Categories", [])][:3],
        }

    matches = [to_match(i) for i in items]
    best = matches[0]
    query = name.lower().split()[0] if name.strip() else ""
    similar = [m for m in matches if query and query in m["title"].lower()]
    localities = {m["locality"] for m in similar}
    best["ambiguous"] = len(similar) > 1 and len(localities) > 1
    best["candidates"] = [{"title": m["title"], "address": m["address"]} for m in matches[:5]]
    return best


def haversine_km(a: list, b: list) -> float:
    lon1, lat1, lon2, lat2 = map(math.radians, [float(a[0]), float(a[1]), float(b[0]), float(b[1])])
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def nearest_office_distance_km(destination: list, offices: list[dict], client=None) -> tuple[str, Decimal] | None:
    """Road distance from the closest office (picked by straight-line distance)."""
    if not destination or not offices:
        return None
    office = min(offices, key=lambda o: haversine_km(o["position"], destination))
    try:
        if client is None:
            import boto3

            client = boto3.client("geo-routes")
        resp = client.calculate_routes(
            Origin=[float(x) for x in office["position"]],
            Destination=[float(x) for x in destination],
            TravelMode="Car",
        )
        meters = resp["Routes"][0]["Summary"]["Distance"]
        km = Decimal(str(round(meters / 1000, 1)))
    except Exception:  # noqa: BLE001 - fall back to straight line * 1.3 road factor
        log.exception("route calculation failed; using straight-line estimate")
        km = Decimal(str(round(haversine_km(office["position"], destination) * 1.3, 1)))
    return office["name"], km
