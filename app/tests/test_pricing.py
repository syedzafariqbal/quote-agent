from decimal import Decimal

import pytest

from quote_core.catalog import CatalogError, validate_catalog
from quote_core.pricing import PricingError, PricingInput, approval_level, is_reverse_charge, price, tier_for


@pytest.mark.parametrize("employees,tier", [(1, "Small"), (49, "Small"), (50, "Mid"), (249, "Mid"),
                                            (250, "Large"), (10_000, "Large")])
def test_tier_boundaries(catalog, employees, tier):
    assert tier_for(catalog, employees) == tier


def test_small_default_package_math(catalog):
    q = price(catalog, PricingInput(employees=20))
    # MGD 12*20=240, HLP 15*20=300 -> 540/month; ONB 1500 one-time
    assert q.recurring_monthly == Decimal("540.00")
    assert q.recurring_term_total == Decimal("6480.00")
    assert q.one_time_total == Decimal("1500.00")
    assert q.net_total == Decimal("7980.00")
    assert q.vat_amount == Decimal("1516.20")
    assert q.gross_total == Decimal("9496.20")
    assert q.approval_level == "none"


def test_term_discount_applies_to_recurring_only(catalog):
    q = price(catalog, PricingInput(employees=20, term_months=36))
    assert q.recurring_term_total == Decimal("19440.00")
    assert q.term_discount_amount == Decimal("1944.00")
    assert q.subtotal == Decimal("19440.00") - Decimal("1944.00") + Decimal("1500.00")


def test_sites_priced_per_site(catalog):
    q = price(catalog, PricingInput(employees=100, sites=3))
    backup = next(li for li in q.line_items if li.sku == "BKP-001")
    assert backup.quantity == 3 and backup.monthly_amount == Decimal("450.00")


def test_dependency_and_exclusion_rules(catalog):
    with pytest.raises(PricingError, match="requires"):
        price(catalog, PricingInput(employees=10, skus=["SEC-001"]))
    with pytest.raises(PricingError, match="cannot be combined"):
        price(catalog, PricingInput(employees=10, skus=["MGD-001", "HLP-001", "HLP-PRM"]))
    with pytest.raises(PricingError, match="unknown"):
        price(catalog, PricingInput(employees=10, skus=["NOPE"]))


def test_invalid_inputs(catalog):
    with pytest.raises(PricingError):
        price(catalog, PricingInput(employees=0))
    with pytest.raises(PricingError, match="term"):
        price(catalog, PricingInput(employees=10, term_months=18))
    with pytest.raises(PricingError, match="discount"):
        price(catalog, PricingInput(employees=10, discount_pct=Decimal("31")))


def test_approval_levels(catalog):
    assert approval_level(catalog, Decimal("5"), Decimal("1000")) == "none"
    assert approval_level(catalog, Decimal("12"), Decimal("1000")) == "manager"
    assert approval_level(catalog, Decimal("25"), Decimal("1000")) == "finance"
    assert approval_level(catalog, Decimal("0"), Decimal("60000")) == "manager"
    assert approval_level(catalog, Decimal("12"), Decimal("200000")) == "finance"


def test_reverse_charge(catalog):
    assert is_reverse_charge(catalog, "AT", "ATU12345678")
    assert not is_reverse_charge(catalog, "AT", None)
    assert not is_reverse_charge(catalog, "DE", "DE123456789")  # domestic
    assert not is_reverse_charge(catalog, "US", "US123")  # not EU
    assert not is_reverse_charge(catalog, "AT", "DE123456789")  # prefix mismatch
    q = price(catalog, PricingInput(employees=20, country="AT", customer_vat_id="ATU12345678"))
    assert q.reverse_charge and q.vat_amount == 0 and q.gross_total == q.net_total


def test_travel_fee(catalog):
    q = price(catalog, PricingInput(employees=20, travel_km=Decimal("150")))
    trv = next(li for li in q.line_items if li.sku == "TRV-001")
    # (150-50) km * 2 directions * 2 trips * 0.6
    assert trv.term_amount == Decimal("240.00")
    q2 = price(catalog, PricingInput(employees=20, travel_km=Decimal("30")))
    assert all(li.sku != "TRV-001" for li in q2.line_items)


def test_german_labels(catalog):
    q = price(catalog, PricingInput(employees=20, language="de"))
    assert q.line_items[0].name == "Managed Cloud Betrieb"


def test_catalog_validation_catches_gaps(catalog):
    import copy

    bad = copy.deepcopy(catalog)
    bad["tiers"][1]["minEmployees"] = 60
    with pytest.raises(CatalogError, match="gap"):
        validate_catalog(bad)
    bad = copy.deepcopy(catalog)
    bad["services"][1]["requires"] = ["XXX"]
    with pytest.raises(CatalogError, match="unknown sku"):
        validate_catalog(bad)
