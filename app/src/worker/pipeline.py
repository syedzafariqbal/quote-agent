"""The agent pipeline for one SQS message.

  create:  REQUESTED -> PROCESSING -> research (Location + headcount)
             -> low confidence / ambiguous -> NEEDS_REVIEW (stop)
             -> price -> summary -> PDF -> DRAFT or PENDING_APPROVAL
  reprice: (API already moved the quote to PROCESSING) -> price -> PDF -> new version

Each priced run writes a new immutable version (V#00n). Revising a SENT quote marks the
previous version `supersededBy` so the old customer link shows "newer version available".
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from quote_core import location, notify, writer
from quote_core.catalog import load_catalog
from quote_core.estimator import Estimate, EstimateCache, estimate_employees
from quote_core.lifecycle import SYSTEM, ConcurrentModification, InvalidTransition, QuoteRepo, Status
from quote_core.pricing import PricingInput, price
from quote_core.serde import to_dynamo

log = logging.getLogger(__name__)
WORKER = "worker"


@dataclass
class Deps:
    repo: QuoteRepo
    cache: EstimateCache | None
    s3: Any
    quotes_bucket: str
    catalog_loader: Callable[[], dict]
    estimator_model_id: str
    writer_model_id: str
    confidence_threshold: float
    approver_email: str = ""
    resolve_company: Callable[..., dict | None] = location.resolve_company
    route_distance: Callable[..., Any] = location.nearest_office_distance_km
    render: Callable[..., tuple[bytes, str]] | None = None  # (html_kwargs) -> (pdf, sha)
    bedrock: Any = None
    extra: dict = field(default_factory=dict)


def default_deps(settings) -> Deps:
    import boto3

    repo = QuoteRepo(settings.table_name)
    return Deps(
        repo=repo,
        cache=EstimateCache(repo.table),
        s3=boto3.client("s3"),
        quotes_bucket=settings.quotes_bucket,
        catalog_loader=lambda: load_catalog(settings.catalog_bucket, settings.catalog_key),
        estimator_model_id=settings.estimator_model_id,
        writer_model_id=settings.writer_model_id,
        confidence_threshold=settings.confidence_threshold,
        approver_email=settings.approver_email,
    )


def _default_render(**kwargs) -> tuple[bytes, str]:
    from quote_core.pdf import render_html, render_pdf

    return render_pdf(render_html(**kwargs))


def handle_message(msg: dict, deps: Deps) -> str:
    """Process one message. Returns the resulting status (for logs/tests)."""
    quote_id = msg["quoteId"]
    kind = msg.get("type", "create")
    repo = deps.repo

    if kind == "create":
        try:
            repo.transition(quote_id, Status.PROCESSING, actor=WORKER, roles={SYSTEM},
                            expected=Status.REQUESTED, reason="worker picked up request")
        except (InvalidTransition, ConcurrentModification):
            meta = repo.get_quote(quote_id)
            if meta["status"] != Status.PROCESSING:
                log.info("quote %s already %s; skipping duplicate message", quote_id, meta["status"])
                return meta["status"]
            # PROCESSING already: a previous attempt crashed mid-way; continue (idempotent below)

    meta = repo.get_quote(quote_id)
    if meta["status"] != Status.PROCESSING:
        log.info("quote %s is %s, not PROCESSING; skipping", quote_id, meta["status"])
        return meta["status"]

    request = meta.get("request", {})
    research = meta.get("research")

    if research is None or kind == "create":
        research = _research(meta, deps)
        est = research["estimate"]
        needs_review = (
            not request.get("employees")
            and (float(est["confidence"]) < deps.confidence_threshold or research.get("ambiguous"))
        )
        if needs_review:
            repo.transition(quote_id, Status.NEEDS_REVIEW, actor=WORKER, roles={SYSTEM},
                            expected=Status.PROCESSING, set_fields={"research": research},
                            reason=f"confidence {est['confidence']} / ambiguous={research.get('ambiguous')}")
            notify.send_email(meta.get("createdByEmail"), f"Quote for {meta['clientName']} needs review",
                              notify.simple_html("Headcount needs confirmation", [
                                  f"Estimated {est['estimate']} employees (range {est['min']}–{est['max']}), "
                                  f"confidence {est['confidence']}.",
                                  "Confirm or override the headcount to continue."]))
            return Status.NEEDS_REVIEW

    return _build_version(meta, research, deps)


def _research(meta: dict, deps: Deps) -> dict:
    request = meta.get("request", {})
    company = deps.resolve_company(meta["clientName"], request.get("country"))
    hints = {k: request[k] for k in ("industry", "website", "notes") if request.get(k)}
    est: Estimate = estimate_employees(
        meta["clientName"], user_employees=request.get("employees"), company=company, hints=hints,
        cache=deps.cache, model_id=deps.estimator_model_id, bedrock=deps.bedrock,
    )
    return to_dynamo({
        "company": company,
        "ambiguous": bool(company and company.get("ambiguous")),
        "estimate": {**est.to_item()},
        "researchedAt": datetime.now(UTC).isoformat(timespec="seconds"),
    })


def _build_version(meta: dict, research: dict, deps: Deps) -> str:
    quote_id = meta["quoteId"]
    request = meta.get("request", {})
    catalog = deps.catalog_loader()
    est = dict(research["estimate"])
    company = research.get("company") or {}

    if request.get("employees"):
        employees = int(request["employees"])
        if est.get("source") not in ("user", "actual"):
            est = {**est, "source": "confirmed", "estimate": employees}
    else:
        employees = int(est["estimate"])

    country = (request.get("country") or company.get("country") or catalog["tax"]["sellerCountry"]).upper()
    travel_km, office = None, None
    if catalog.get("travel", {}).get("enabled") and company.get("position") and request.get("includeTravel", True):
        res = deps.route_distance(company["position"], catalog["travel"]["offices"])
        if res:
            office, travel_km = res

    lang = request.get("language", "en")
    priced = price(catalog, PricingInput(
        employees=employees,
        country=country,
        sites=int(request.get("sites", 1)),
        term_months=int(request.get("termMonths", 12)),
        discount_pct=Decimal(str(request.get("discountPct", 0))),
        skus=request.get("skus"),
        customer_vat_id=request.get("customerVatId"),
        travel_km=travel_km,
        language=lang,
    ))

    summary = writer.write_summary(meta["clientName"], priced, lang, deps.writer_model_id, deps.bedrock)
    prev = int(meta.get("currentVersion", 0))
    n = prev + 1
    render = deps.render or _default_render
    pdf_bytes, sha = render(quote_id=quote_id, version=n, client_name=meta["clientName"],
                            client_address=company.get("address", ""), priced=priced, estimate=est,
                            summary=summary, lang=lang)

    key = f"quotes/{datetime.now(UTC):%Y}/{quote_id}/v{n:03d}.pdf"
    deps.s3.put_object(Bucket=deps.quotes_bucket, Key=key, Body=pdf_bytes, ContentType="application/pdf",
                       ChecksumAlgorithm="SHA256", Metadata={"quote-id": quote_id, "version": str(n)})

    target = Status.PENDING_APPROVAL if priced.approval_level != "none" else Status.DRAFT
    version_payload = to_dynamo({
        "priced": priced.to_dict(),
        "estimate": est,
        "summary": summary,
        "pdfKey": key,
        "pdfSha256": sha,
        "travelFrom": office,
        "request": request,
    })
    extra = [deps.repo.version_put_item(quote_id, n, version_payload)]
    if prev:
        extra.append(deps.repo.supersede_item(quote_id, prev, n))

    deps.repo.transition(
        quote_id, target, actor=WORKER, roles={SYSTEM}, expected=Status.PROCESSING,
        reason=f"priced v{n} ({priced.tier}, net {priced.net_total} {priced.currency})",
        set_fields={"currentVersion": n, "research": research, "approvalLevel": priced.approval_level,
                    "netTotal": priced.net_total, "grossTotal": priced.gross_total, "currency": priced.currency},
        extra_items=extra,
    )

    if target == Status.PENDING_APPROVAL:
        notify.send_email(deps.approver_email, f"Approval needed: quote for {meta['clientName']}",
                          notify.simple_html("Quote awaiting approval", [
                              f"Client: {meta['clientName']}", f"Net total: {priced.net_total} {priced.currency}",
                              f"Discount: {priced.discount_pct}% · Required level: {priced.approval_level}",
                              f"Quote ID: {quote_id}"]))
    else:
        notify.send_email(meta.get("createdByEmail"), f"Quote ready: {meta['clientName']}",
                          notify.simple_html("Your quote is ready", [
                              f"Version {n}, net {priced.net_total} {priced.currency}.", f"Quote ID: {quote_id}"]))
    return target


def mark_failed(quote_id: str, deps: Deps, error: str) -> None:
    try:
        deps.repo.transition(quote_id, Status.FAILED, actor=WORKER, roles={SYSTEM}, expected=Status.PROCESSING,
                             reason=error[:500])
    except Exception:  # noqa: BLE001
        log.exception("could not mark %s as FAILED", quote_id)
