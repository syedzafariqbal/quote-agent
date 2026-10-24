"""Short executive summary for the PDF, written by a small (cheap) model.

The model only sees names and the tier. It produces prose; it cannot change any number,
because every price on the PDF comes from the pricing engine.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

FALLBACK = {
    "en": ("Thank you for your interest. Based on an organisation of about {employees} employees, we propose "
           "the {tier} package below, covering {services}. The offer is valid for {days} days."),
    "de": ("Vielen Dank für Ihr Interesse. Für eine Organisation mit rund {employees} Mitarbeitenden schlagen wir "
           "das folgende {tier}-Paket vor: {services}. Das Angebot ist {days} Tage gültig."),
}


def fallback_summary(client_name: str, priced, lang: str) -> str:
    services = ", ".join(li.name for li in priced.line_items if li.sku != "TRV-001")
    return FALLBACK.get(lang, FALLBACK["en"]).format(
        employees=priced.employees, tier=priced.tier, services=services, days=priced.validity_days
    )


def write_summary(client_name: str, priced, lang: str, model_id: str, bedrock=None) -> str:
    if not model_id:
        return fallback_summary(client_name, priced, lang)
    services = ", ".join(li.name for li in priced.line_items if li.sku != "TRV-001")
    language = "German (formal 'Sie')" if lang == "de" else "English"
    prompt = (
        f"Write a 2-3 sentence executive summary in {language} for a sales quotation to the company "
        f"named in <client> tags. The proposed package is '{priced.tier}' for about {priced.employees} "
        f"employees and includes: {services}. Do not mention any prices, discounts or numbers other than "
        f"the employee count. Plain text only.\n<client>{client_name[:120]}</client>"
    )
    try:
        if bedrock is None:
            import boto3

            bedrock = boto3.client("bedrock-runtime")
        resp = bedrock.converse(
            modelId=model_id,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 300, "temperature": 0.3},
        )
        text = "".join(b.get("text", "") for b in resp["output"]["message"]["content"]).strip()
        if not text or any(sym in text for sym in ("€", "EUR", "%")):
            return fallback_summary(client_name, priced, lang)
        return text[:900]
    except Exception:  # noqa: BLE001
        log.exception("summary generation failed, using fallback")
        return fallback_summary(client_name, priced, lang)
