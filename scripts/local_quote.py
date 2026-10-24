"""Render a quote PDF locally with no AWS calls (phase 1 of the build order).

    python scripts/local_quote.py "Acme Logistics GmbH" --employees 120 --term 24 --lang de

Needs WeasyPrint's system libraries (or run it inside the worker container, see README).
"""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app" / "src"))

from quote_core.catalog import parse_catalog  # noqa: E402
from quote_core.pdf import render_html, render_pdf  # noqa: E402
from quote_core.pricing import PricingInput, price  # noqa: E402
from quote_core.serde import dumps  # noqa: E402
from quote_core.writer import fallback_summary  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("client")
    ap.add_argument("--employees", type=int, default=120)
    ap.add_argument("--sites", type=int, default=1)
    ap.add_argument("--term", type=int, default=12, choices=[12, 24, 36])
    ap.add_argument("--discount", type=Decimal, default=Decimal("0"))
    ap.add_argument("--country", default="DE")
    ap.add_argument("--vat-id")
    ap.add_argument("--travel-km", type=Decimal)
    ap.add_argument("--lang", default="en", choices=["en", "de"])
    ap.add_argument("--catalog", default=str(ROOT / "catalog" / "catalog.v2026-10.json"))
    ap.add_argument("--out", default=str(ROOT / "out" / "quote.pdf"))
    args = ap.parse_args()

    catalog = parse_catalog(Path(args.catalog).read_text())
    priced = price(catalog, PricingInput(
        employees=args.employees, sites=args.sites, term_months=args.term, discount_pct=args.discount,
        country=args.country, customer_vat_id=args.vat_id, travel_km=args.travel_km, language=args.lang,
    ))
    html = render_html(
        quote_id="local-0000-0000", version=1, client_name=args.client, client_address="",
        priced=priced, estimate={"source": "user", "estimate": args.employees, "min": args.employees,
                                 "max": args.employees},
        summary=fallback_summary(args.client, priced, args.lang), lang=args.lang,
    )
    pdf, sha = render_pdf(html)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(pdf)
    print(json.dumps({"pdf": str(out), "sha256": sha, "net": priced.net_total, "gross": priced.gross_total,
                      "approval": priced.approval_level}, default=str, indent=2))
    out.with_suffix(".json").write_text(dumps(priced.to_dict()))


if __name__ == "__main__":
    main()
