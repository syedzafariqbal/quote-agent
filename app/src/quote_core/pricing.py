"""Deterministic pricing engine.

The LLM never touches this module: it only supplies a headcount. Every number on a
quote comes from here, so quotes are reproducible and auditable.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

EU_COUNTRIES = {
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE", "IT",
    "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE",
}
VAT_ID_RE = re.compile(r"^[A-Z]{2}[A-Z0-9+*]{2,12}$")
APPROVAL_ORDER = ["none", "manager", "finance"]

CENT = Decimal("0.01")


def q2(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_HALF_UP)


class PricingError(ValueError):
    pass


@dataclass
class PricingInput:
    employees: int
    country: str = "DE"
    sites: int = 1
    term_months: int = 12
    discount_pct: Decimal = Decimal("0")
    skus: list[str] | None = None
    customer_vat_id: str | None = None
    travel_km: Decimal | None = None  # one-way road distance from nearest office
    language: str = "en"


@dataclass
class LineItem:
    sku: str
    name: str
    description: str
    pricing: str
    quantity: int
    unit_price: Decimal
    monthly_amount: Decimal  # 0 for one-time items
    term_amount: Decimal  # monthly * term, or the one-time amount


@dataclass
class PricedQuote:
    catalog_version: str
    currency: str
    tier: str
    employees: int
    sites: int
    term_months: int
    line_items: list[LineItem] = field(default_factory=list)
    recurring_monthly: Decimal = Decimal("0")
    recurring_term_total: Decimal = Decimal("0")
    term_discount_pct: Decimal = Decimal("0")
    term_discount_amount: Decimal = Decimal("0")
    one_time_total: Decimal = Decimal("0")
    subtotal: Decimal = Decimal("0")
    discount_pct: Decimal = Decimal("0")
    discount_amount: Decimal = Decimal("0")
    net_total: Decimal = Decimal("0")
    vat_rate: Decimal = Decimal("0")
    vat_amount: Decimal = Decimal("0")
    reverse_charge: bool = False
    gross_total: Decimal = Decimal("0")
    approval_level: str = "none"
    validity_days: int = 30

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def tier_for(catalog: dict, employees: int) -> str:
    for t in catalog["tiers"]:
        if t["minEmployees"] <= employees <= t["maxEmployees"]:
            return t["name"]
    raise PricingError(f"no tier for {employees} employees")


def _check_selection(catalog: dict, skus: list[str]) -> None:
    by_sku = {s["sku"]: s for s in catalog["services"]}
    unknown = [s for s in skus if s not in by_sku]
    if unknown:
        raise PricingError(f"unknown services: {unknown}")
    if len(set(skus)) != len(skus):
        raise PricingError("duplicate services in selection")
    chosen = set(skus)
    for sku in skus:
        svc = by_sku[sku]
        missing = [r for r in svc.get("requires", []) if r not in chosen]
        if missing:
            raise PricingError(f"{sku} requires {missing}")
        clash = [x for x in svc.get("excludes", []) if x in chosen]
        if clash:
            raise PricingError(f"{sku} cannot be combined with {clash}")


def is_reverse_charge(catalog: dict, country: str, vat_id: str | None) -> bool:
    seller = catalog["tax"]["sellerCountry"]
    if not vat_id or country == seller or country not in EU_COUNTRIES:
        return False
    vat_id = vat_id.replace(" ", "").upper()
    prefix = "EL" if country == "GR" else country
    return bool(VAT_ID_RE.match(vat_id)) and vat_id.startswith(prefix)


def approval_level(catalog: dict, discount_pct: Decimal, net_total: Decimal) -> str:
    rules = catalog["approval"]
    by_discount = "none"
    if discount_pct > Decimal(str(rules["financeDiscountPct"])):
        by_discount = "finance"
    elif discount_pct > Decimal(str(rules["managerDiscountPct"])):
        by_discount = "manager"
    by_total = "none"
    if net_total > Decimal(str(rules["financeNetTotal"])):
        by_total = "finance"
    elif net_total > Decimal(str(rules["managerNetTotal"])):
        by_total = "manager"
    return max(by_discount, by_total, key=APPROVAL_ORDER.index)


def price(catalog: dict, inp: PricingInput) -> PricedQuote:
    if inp.employees < 1:
        raise PricingError("employees must be >= 1")
    if inp.sites < 1:
        raise PricingError("sites must be >= 1")
    term_key = str(inp.term_months)
    if term_key not in catalog["termDiscounts"]:
        raise PricingError(f"term must be one of {sorted(catalog['termDiscounts'], key=int)} months")
    discount_pct = Decimal(str(inp.discount_pct))
    max_discount = Decimal(str(catalog.get("maxDiscountPct", 100)))
    if not Decimal("0") <= discount_pct <= max_discount:
        raise PricingError(f"discount must be between 0 and {max_discount}%")

    lang = inp.language if inp.language in ("en", "de") else "en"
    tier = tier_for(catalog, inp.employees)
    skus = list(inp.skus) if inp.skus else list(catalog["defaultPackages"][tier])
    _check_selection(catalog, skus)
    by_sku = {s["sku"]: s for s in catalog["services"]}
    term = Decimal(inp.term_months)

    out = PricedQuote(
        catalog_version=catalog["version"],
        currency=catalog["currency"],
        tier=tier,
        employees=inp.employees,
        sites=inp.sites,
        term_months=inp.term_months,
        validity_days=int(catalog.get("validityDays", 30)),
    )

    for sku in skus:
        svc = by_sku[sku]
        unit = Decimal(str(svc["tierPrices"][tier] if "tierPrices" in svc else svc["price"]))
        model = svc["pricing"]
        if model == "per_employee_month":
            qty, monthly = inp.employees, unit * inp.employees
        elif model == "per_site_month":
            qty, monthly = inp.sites, unit * inp.sites
        else:  # one_time
            qty, monthly = 1, Decimal("0")
        term_amount = monthly * term if model != "one_time" else unit
        out.line_items.append(
            LineItem(
                sku=sku,
                name=svc["name"].get(lang, svc["name"]["en"]),
                description=svc.get("description", {}).get(lang, ""),
                pricing=model,
                quantity=qty,
                unit_price=q2(unit),
                monthly_amount=q2(monthly),
                term_amount=q2(term_amount),
            )
        )

    travel = catalog.get("travel", {})
    if travel.get("enabled") and inp.travel_km is not None:
        billable_km = max(Decimal("0"), Decimal(str(inp.travel_km)) - Decimal(str(travel["freeKm"])))
        if billable_km > 0:
            trips = int(travel.get("roundTrips", 1))
            rate = Decimal(str(travel["ratePerKm"]))
            amount = q2(billable_km * 2 * trips * rate)
            out.line_items.append(
                LineItem(
                    sku="TRV-001",
                    name="On-site travel" if lang == "en" else "Anfahrt vor Ort",
                    description=(
                        f"{trips} round trip(s), {q2(billable_km)} billable km each way"
                        if lang == "en"
                        else f"{trips} Hin- und Rückfahrt(en), {q2(billable_km)} abrechenbare km pro Strecke"
                    ),
                    pricing="one_time",
                    quantity=trips,
                    unit_price=q2(amount / trips),
                    monthly_amount=Decimal("0"),
                    term_amount=amount,
                )
            )

    out.recurring_monthly = q2(sum((li.monthly_amount for li in out.line_items), Decimal("0")))
    out.recurring_term_total = q2(out.recurring_monthly * term)
    out.term_discount_pct = Decimal(str(catalog["termDiscounts"][term_key])) * 100
    out.term_discount_amount = q2(out.recurring_term_total * out.term_discount_pct / 100)
    out.one_time_total = q2(
        sum((li.term_amount for li in out.line_items if li.pricing == "one_time"), Decimal("0"))
    )
    out.subtotal = q2(out.recurring_term_total - out.term_discount_amount + out.one_time_total)
    out.discount_pct = discount_pct
    out.discount_amount = q2(out.subtotal * discount_pct / 100)
    out.net_total = q2(out.subtotal - out.discount_amount)

    country = inp.country.upper()
    out.reverse_charge = is_reverse_charge(catalog, country, inp.customer_vat_id)
    if out.reverse_charge:
        out.vat_rate = Decimal("0")
    else:
        # domestic VAT applies to non-reverse-charge B2B sales in this simplified model
        seller = catalog["tax"]["sellerCountry"]
        rates = catalog["tax"]["vatByCountry"]
        out.vat_rate = Decimal(str(rates.get(seller, catalog["tax"]["defaultVat"])))
    out.vat_amount = q2(out.net_total * out.vat_rate)
    out.gross_total = q2(out.net_total + out.vat_amount)
    out.approval_level = approval_level(catalog, discount_pct, out.net_total)
    return out
