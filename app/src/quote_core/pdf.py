"""Render the quote PDF from an HTML template (Jinja2 + WeasyPrint). Worker container only."""

from __future__ import annotations

import hashlib
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

TEMPLATES = Path(__file__).parent / "templates"

LABELS = {
    "en": {
        "title": "Sales Quotation", "quote_no": "Quote no.", "date": "Date", "valid_until": "Valid until",
        "version": "Version", "client": "Prepared for", "summary": "Summary", "assumptions": "Assumptions",
        "employees": "Employees (basis for pricing)", "estimated": "estimated", "confirmed": "confirmed",
        "sites": "Sites", "term": "Contract term", "months": "months", "catalog": "Price list",
        "service": "Service", "qty": "Qty", "unit": "Unit price", "monthly": "Monthly", "total": "Total (term)",
        "one_time": "one-time", "recurring_term": "Recurring services over term", "term_discount": "Term discount",
        "one_time_total": "One-time items", "subtotal": "Subtotal", "discount": "Discount",
        "net": "Net total", "vat": "VAT", "gross": "Total incl. VAT",
        "reverse_charge": "Reverse charge: VAT to be accounted for by the recipient (Art. 196 VAT Directive).",
        "terms": "Terms", "terms_text": "Prices in {cur}, excluding VAT unless stated. Recurring services are "
        "billed monthly in advance. This quotation is non-binding until accepted in writing or online.",
        "accept": "Accept online", "source": "Headcount source",
    },
    "de": {
        "title": "Angebot", "quote_no": "Angebotsnr.", "date": "Datum", "valid_until": "Gültig bis",
        "version": "Version", "client": "Erstellt für", "summary": "Zusammenfassung", "assumptions": "Annahmen",
        "employees": "Mitarbeitende (Preisgrundlage)", "estimated": "geschätzt", "confirmed": "bestätigt",
        "sites": "Standorte", "term": "Vertragslaufzeit", "months": "Monate", "catalog": "Preisliste",
        "service": "Leistung", "qty": "Menge", "unit": "Einzelpreis", "monthly": "Monatlich",
        "total": "Gesamt (Laufzeit)", "one_time": "einmalig", "recurring_term": "Laufende Leistungen über Laufzeit",
        "term_discount": "Laufzeitrabatt", "one_time_total": "Einmalige Positionen", "subtotal": "Zwischensumme",
        "discount": "Rabatt", "net": "Nettobetrag", "vat": "USt.", "gross": "Gesamtbetrag inkl. USt.",
        "reverse_charge": "Steuerschuldnerschaft des Leistungsempfängers (Reverse Charge, Art. 196 MwStSystRL).",
        "terms": "Bedingungen", "terms_text": "Preise in {cur}, zzgl. USt., sofern nicht anders angegeben. "
        "Laufende Leistungen werden monatlich im Voraus berechnet. Dieses Angebot ist bis zur schriftlichen "
        "oder Online-Annahme unverbindlich.",
        "accept": "Online annehmen", "source": "Quelle Mitarbeiterzahl",
    },
}


def money(value: Decimal | float | int, lang: str) -> str:
    s = f"{Decimal(str(value)):,.2f}"
    if lang == "de":
        s = s.replace(",", "X").replace(".", ",").replace("X", ".")
    return s


def render_html(*, quote_id: str, version: int, client_name: str, client_address: str, priced, estimate: dict,
                summary: str, lang: str, issued: date | None = None, company_name: str = "Your Company GmbH") -> str:
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]))
    env.filters["money"] = lambda v: money(v, lang)
    issued = issued or date.today()
    fmt = "%d.%m.%Y" if lang == "de" else "%d %b %Y"
    return env.get_template("quote.html").render(
        L=LABELS.get(lang, LABELS["en"]), lang=lang, quote_id=quote_id, quote_no=f"Q-{quote_id[:8].upper()}",
        version=version, client_name=client_name, client_address=client_address, q=priced, estimate=estimate,
        summary=summary, issued=issued.strftime(fmt),
        valid_until=(issued + timedelta(days=priced.validity_days)).strftime(fmt),
        company_name=company_name,
    )


def render_pdf(html: str) -> tuple[bytes, str]:
    """Returns (pdf_bytes, sha256_hex)."""
    from weasyprint import HTML

    data = HTML(string=html, base_url=str(TEMPLATES)).write_pdf()
    return data, hashlib.sha256(data).hexdigest()
