"""HTTP API (API Gateway v2, payload format 2.0) for the quote lifecycle.

Authenticated routes (Cognito JWT; roles from the `cognito:groups` claim):
  POST /quotes                      create (SalesRep)         -> REQUESTED, enqueued
  GET  /quotes?status=DRAFT         list by status
  GET  /quotes/{id}                 quote + current version + presigned PDF + allowed actions
  GET  /quotes/{id}/audit           audit trail
  POST /quotes/{id}/confirm         NEEDS_REVIEW -> PROCESSING with confirmed headcount
  POST /quotes/{id}/revise          DRAFT/PENDING/APPROVED/SENT/VIEWED/FAILED -> PROCESSING (new version)
  POST /quotes/{id}/approve         PENDING_APPROVAL -> APPROVED (Manager/Finance by level)
  POST /quotes/{id}/request-changes PENDING_APPROVAL -> DRAFT
  POST /quotes/{id}/send            DRAFT|APPROVED -> SENT (email link, schedule reminders/expiry)
  POST /quotes/{id}/reject          SENT|VIEWED -> REJECTED (rep records the customer's no)
  POST /quotes/{id}/cancel          any open -> CANCELLED
  POST /quotes/{id}/actual-headcount  after ACCEPTED: feed the estimation feedback loop
Public routes (HMAC-signed link, no login):
  GET  /public/quotes/{id}?t=...    customer page (SENT -> VIEWED)
  POST /public/quotes/{id}/accept   -> ACCEPTED
  POST /public/quotes/{id}/decline  -> REJECTED
"""

from __future__ import annotations

import base64
import json
import logging
import time
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from html import escape
from typing import Any
from urllib.parse import parse_qs

import boto3

from quote_core import notify, signing
from quote_core.config import settings
from quote_core.estimator import Estimate, EstimateCache
from quote_core.lifecycle import (
    CUSTOMER,
    REVISABLE,
    LifecycleError,
    NotFound,
    QuoteRepo,
    Status,
    allowed_targets,
)
from quote_core.serde import dumps, to_dynamo

log = logging.getLogger()
log.setLevel(logging.INFO)

REQUEST_FIELDS = {
    "country": str, "sites": int, "termMonths": int, "discountPct": float, "skus": list,
    "customerVatId": str, "language": str, "employees": int, "customerEmail": str, "customerName": str,
    "industry": str, "website": str, "notes": str, "includeTravel": bool,
}
REJECT_REASONS = {"price", "scope", "timing", "competitor", "no_decision", "other"}


class HttpError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


@lru_cache(maxsize=1)
def repo() -> QuoteRepo:
    return QuoteRepo(settings().table_name)


@lru_cache(maxsize=1)
def clients() -> dict[str, Any]:
    return {"sqs": boto3.client("sqs"), "s3": boto3.client("s3"), "scheduler": boto3.client("scheduler")}


# ------------------------------------------------------------------ helpers

def json_response(status: int, body: Any) -> dict:
    return {"statusCode": status, "headers": {"content-type": "application/json"}, "body": dumps(body)}


def html_response(status: int, html: str) -> dict:
    return {"statusCode": status, "headers": {"content-type": "text/html; charset=utf-8",
                                              "x-frame-options": "DENY", "cache-control": "no-store"},
            "body": html}


def parse_body(event: dict) -> dict:
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode()
    ctype = (event.get("headers") or {}).get("content-type", "")
    if "application/x-www-form-urlencoded" in ctype:
        return {k: v[0] for k, v in parse_qs(raw).items()}
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise HttpError(400, "body must be JSON") from None
    if not isinstance(data, dict):
        raise HttpError(400, "body must be a JSON object")
    return data


def identity(event: dict) -> tuple[str, str | None, set[str]]:
    claims = event.get("requestContext", {}).get("authorizer", {}).get("jwt", {}).get("claims", {})
    raw = claims.get("cognito:groups", "")
    if isinstance(raw, list):
        groups = set(raw)
    else:  # HTTP API flattens arrays to "[A B]"
        groups = {g for g in raw.strip("[]").replace(",", " ").split() if g}
    user = claims.get("email") or claims.get("username") or claims.get("cognito:username") or "unknown"
    return user, claims.get("email"), groups


def require(groups: set[str], allowed: set[str]) -> None:
    if not groups & allowed:
        raise HttpError(403, f"requires one of {sorted(allowed)}")


def clean_request(data: dict, partial: bool = False) -> dict:
    out: dict[str, Any] = {}
    for key, typ in REQUEST_FIELDS.items():
        if key not in data or data[key] is None:
            continue
        val = data[key]
        try:
            if typ is int:
                val = int(val)
                if val < 1:
                    raise ValueError
            elif typ is float:
                val = Decimal(str(val))
            elif typ is bool:
                val = bool(val)
            elif typ is list:
                if not isinstance(val, list) or not all(isinstance(s, str) for s in val):
                    raise ValueError
            else:
                val = str(val).strip()[:300]
        except (ValueError, InvalidOperation):
            raise HttpError(400, f"invalid value for {key}") from None
        out[key] = val
    if "language" in out and out["language"] not in ("en", "de"):
        raise HttpError(400, "language must be 'en' or 'de'")
    if "country" in out:
        out["country"] = out["country"].upper()[:2]
    return out


def enqueue(message: dict) -> None:
    clients()["sqs"].send_message(QueueUrl=settings().queue_url, MessageBody=json.dumps(message))


def presign(key: str, minutes: int = 15) -> str:
    return clients()["s3"].generate_presigned_url(
        "get_object", Params={"Bucket": settings().quotes_bucket, "Key": key}, ExpiresIn=minutes * 60
    )


def current_version(quote: dict) -> dict | None:
    n = int(quote.get("currentVersion", 0))
    return repo().get_version(quote["quoteId"], n) if n else None


def schedule(quote_id: str, version: int, kind: str, at: datetime, payload: dict) -> None:
    if at <= datetime.now(UTC):
        return
    cfg = settings()
    clients()["scheduler"].create_schedule(
        Name=f"{quote_id}-{kind}-v{version}",
        GroupName=cfg.scheduler_group,
        ScheduleExpression=f"at({at.strftime('%Y-%m-%dT%H:%M:%S')})",
        ScheduleExpressionTimezone="UTC",
        FlexibleTimeWindow={"Mode": "OFF"},
        ActionAfterCompletion="DELETE",
        Target={"Arn": cfg.scheduler_target_arn, "RoleArn": cfg.scheduler_role_arn,
                "Input": json.dumps({"quoteId": quote_id, "version": version, "kind": kind, **payload})},
    )


# ------------------------------------------------------------------ authenticated routes

def create_quote(event, user, email, groups):
    require(groups, {"SalesRep", "Admin"})
    data = parse_body(event)
    client_name = str(data.get("clientName", "")).strip()
    if not 2 <= len(client_name) <= 120:
        raise HttpError(400, "clientName is required (2-120 chars)")
    request = to_dynamo(clean_request(data))
    idem = (event.get("headers") or {}).get("idempotency-key")
    new_id = str(uuid.uuid4())
    quote_id = repo().create_quote(new_id, client_name, request, user, email,
                                   idempotency_key=idem[:100] if idem else None)
    if quote_id == new_id:  # not an idempotent replay
        enqueue({"type": "create", "quoteId": quote_id})
    quote = repo().get_quote(quote_id)
    return json_response(202, {"quoteId": quote_id, "status": quote["status"]})


def list_quotes(event, user, email, groups):
    status = (event.get("queryStringParameters") or {}).get("status", "DRAFT")
    try:
        items = repo().list_by_status(status)
    except ValueError:
        raise HttpError(400, f"unknown status {status}") from None
    keep = ("quoteId", "clientName", "status", "updatedAt", "netTotal", "currency", "currentVersion", "createdBy")
    return json_response(200, [{k: i.get(k) for k in keep} for i in items])


def get_quote(event, user, email, groups, quote_id):
    quote = repo().get_quote(quote_id)
    version = current_version(quote)
    body = {"quote": {k: v for k, v in quote.items() if not k.startswith(("PK", "SK", "GSI"))},
            "allowedActions": allowed_targets(quote["status"], groups)}
    if version:
        body["version"] = {k: v for k, v in version.items() if k not in ("PK", "SK")}
        body["pdfUrl"] = presign(version["pdfKey"])
    return json_response(200, body)


def get_audit(event, user, email, groups, quote_id):
    repo().get_quote(quote_id)
    items = repo().list_audit(quote_id)
    return json_response(200, [{k: v for k, v in i.items() if k not in ("PK", "SK")} for i in items])


def confirm(event, user, email, groups, quote_id):
    data = parse_body(event)
    try:
        employees = int(data["employees"])
        assert employees >= 1
    except (KeyError, ValueError, AssertionError):
        raise HttpError(400, "employees (integer >= 1) is required") from None
    quote = repo().get_quote(quote_id)
    if quote["status"] != Status.NEEDS_REVIEW:
        raise HttpError(409, f"quote is {quote['status']}, not NEEDS_REVIEW")
    request = {**quote.get("request", {}), "employees": employees}
    repo().transition(quote_id, Status.PROCESSING, actor=user, roles=groups, expected=Status.NEEDS_REVIEW,
                      set_fields={"request": request}, reason=f"headcount confirmed: {employees}")
    enqueue({"type": "reprice", "quoteId": quote_id})
    return json_response(202, {"quoteId": quote_id, "status": Status.PROCESSING})


def revise(event, user, email, groups, quote_id):
    changes = clean_request(parse_body(event))
    quote = repo().get_quote(quote_id)
    if quote["status"] not in {s.value for s in REVISABLE}:
        raise HttpError(409, f"cannot revise a quote in {quote['status']}")
    request = to_dynamo({**quote.get("request", {}), **changes})
    repo().transition(quote_id, Status.PROCESSING, actor=user, roles=groups, expected=quote["status"],
                      set_fields={"request": request}, reason="revision requested",
                      audit_data=to_dynamo({"changes": changes}))
    enqueue({"type": "reprice", "quoteId": quote_id})
    return json_response(202, {"quoteId": quote_id, "status": Status.PROCESSING})


def approve(event, user, email, groups, quote_id):
    data = parse_body(event)
    quote = repo().get_quote(quote_id)
    level = quote.get("approvalLevel", "manager")
    needed = {"Finance", "Admin"} if level == "finance" else {"Manager", "Finance", "Admin"}
    require(groups, needed)
    if quote.get("createdBy") == user and "Admin" not in groups:
        raise HttpError(403, "you cannot approve your own quote")
    repo().transition(quote_id, Status.APPROVED, actor=user, roles=groups, expected=Status.PENDING_APPROVAL,
                      reason=str(data.get("comment", ""))[:500] or f"approved ({level})",
                      set_fields={"approvedBy": user, "approvedAt": datetime.now(UTC).isoformat(timespec="seconds")})
    notify.send_email(quote.get("createdByEmail"), f"Quote approved: {quote['clientName']}",
                      notify.simple_html("Quote approved", [f"Approved by {user}. You can now send it."]))
    return json_response(200, {"quoteId": quote_id, "status": Status.APPROVED})


def request_changes(event, user, email, groups, quote_id):
    comment = str(parse_body(event).get("comment", "")).strip()
    if not comment:
        raise HttpError(400, "comment is required")
    quote = repo().get_quote(quote_id)
    repo().transition(quote_id, Status.DRAFT, actor=user, roles=groups, expected=Status.PENDING_APPROVAL,
                      reason=f"changes requested: {comment[:500]}")
    notify.send_email(quote.get("createdByEmail"), f"Changes requested: {quote['clientName']}",
                      notify.simple_html("Changes requested", [comment]))
    return json_response(200, {"quoteId": quote_id, "status": Status.DRAFT})


def send_quote(event, user, email, groups, quote_id):
    data = parse_body(event)
    quote = repo().get_quote(quote_id)
    version = current_version(quote)
    if not version:
        raise HttpError(409, "quote has no priced version yet")
    if quote["status"] == Status.DRAFT and quote.get("approvalLevel", "none") != "none":
        raise HttpError(409, "this version requires approval; revise it or wait for approval")
    customer_email = str(data.get("customerEmail") or quote.get("request", {}).get("customerEmail") or "").strip()
    if "@" not in customer_email:
        raise HttpError(400, "customerEmail is required")

    cfg = settings()
    n = int(version["version"])
    validity = int(version["priced"].get("validity_days", 30))
    sent_at = datetime.now(UTC)
    expires_at = sent_at + timedelta(days=validity)
    token = signing.make_token(quote_id, n, int(expires_at.timestamp()), signing.link_secret(cfg.link_secret_param))
    link = f"{cfg.public_base_url}/public/quotes/{quote_id}?t={token}"

    repo().transition(quote_id, Status.SENT, actor=user, roles=groups, expected=quote["status"],
                      reason=f"sent v{n} to {customer_email}",
                      set_fields={"sentAt": sent_at.isoformat(timespec="seconds"),
                                  "expiresAt": expires_at.isoformat(timespec="seconds"),
                                  "GSI1SK": expires_at.isoformat(timespec="seconds"),
                                  "sentVersion": n, "customerEmail": customer_email,
                                  "sentPdfSha256": version["pdfSha256"]})
    for days_left in (7, 2):
        schedule(quote_id, n, f"remind{days_left}", expires_at - timedelta(days=days_left), {"daysLeft": days_left})
    schedule(quote_id, n, "expire", expires_at, {})

    lang = quote.get("request", {}).get("language", "en")
    subject = ("Ihr Angebot" if lang == "de" else "Your quotation") + f" ({quote['clientName']})"
    notify.send_email(customer_email, subject, notify.simple_html(
        "Angebot" if lang == "de" else "Your quotation",
        [f"Valid until {expires_at:%d.%m.%Y}."], link, "Angebot ansehen" if lang == "de" else "View quotation"))
    return json_response(200, {"quoteId": quote_id, "status": Status.SENT, "customerLink": link,
                               "expiresAt": expires_at.isoformat(timespec="seconds")})


def reject(event, user, email, groups, quote_id):
    data = parse_body(event)
    reason = data.get("reasonCode", "other")
    if reason not in REJECT_REASONS:
        raise HttpError(400, f"reasonCode must be one of {sorted(REJECT_REASONS)}")
    repo().transition(quote_id, Status.REJECTED, actor=user, roles=groups,
                      reason=f"{reason}: {str(data.get('note', ''))[:300]}", set_fields={"rejectReason": reason})
    return json_response(200, {"quoteId": quote_id, "status": Status.REJECTED})


def cancel(event, user, email, groups, quote_id):
    note = str(parse_body(event).get("note", ""))[:300]
    repo().transition(quote_id, Status.CANCELLED, actor=user, roles=groups, reason=note or "cancelled")
    return json_response(200, {"quoteId": quote_id, "status": Status.CANCELLED})


def actual_headcount(event, user, email, groups, quote_id):
    require(groups, {"SalesRep", "Admin"})
    try:
        employees = int(parse_body(event)["employees"])
        assert employees >= 1
    except (KeyError, ValueError, AssertionError):
        raise HttpError(400, "employees (integer >= 1) is required") from None
    quote = repo().get_quote(quote_id)
    if quote["status"] != Status.ACCEPTED:
        raise HttpError(409, "actual headcount can only be recorded on ACCEPTED quotes")
    est = (quote.get("research") or {}).get("estimate")
    previous = Estimate.from_item(est) if est else None
    EstimateCache(repo().table).record_actual(quote["clientName"], employees, quote_id, previous)
    repo().update_fields(quote_id, {"actualEmployees": employees})
    return json_response(200, {"quoteId": quote_id, "actualEmployees": employees})


# ------------------------------------------------------------------ public (customer) routes

PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>body{{font-family:Arial,sans-serif;max-width:640px;margin:40px auto;padding:0 16px;color:#1d2330}}
.card{{border:1px solid #d6dde6;border-radius:8px;padding:20px;margin:16px 0}}button{{background:#1f4e79;color:#fff;border:0;
padding:10px 18px;border-radius:4px;font-size:15px;cursor:pointer}}button.secondary{{background:#6b7480}}
input,select{{width:100%;padding:8px;margin:4px 0 12px;box-sizing:border-box}}.muted{{color:#5b6573;font-size:13px}}</style></head>
<body>{body}</body></html>"""


def _public_context(event, quote_id: str, token: str | None) -> tuple[dict, dict, dict]:
    try:
        claims = signing.verify_token(token or "", quote_id, signing.link_secret(settings().link_secret_param))
    except signing.InvalidToken as exc:
        raise HttpError(403, f"This link is not valid: {exc}") from None
    quote = repo().get_quote(quote_id)
    version = repo().get_version(quote_id, int(claims["v"]))
    if not version:
        raise HttpError(404, "version not found")
    return claims, quote, version


def public_view(event, quote_id):
    token = (event.get("queryStringParameters") or {}).get("t")
    claims, quote, version = _public_context(event, quote_id, token)
    status = quote["status"]
    if version.get("supersededBy") or int(quote.get("currentVersion", 0)) != int(claims["v"]):
        body = "<h2>A newer version of this quotation is available</h2><p>Please use the most recent link we sent you.</p>"
        return html_response(410, PAGE.format(title="Quotation updated", body=body))
    if status == Status.SENT:
        repo().transition(quote_id, Status.VIEWED, actor=CUSTOMER, roles={CUSTOMER}, expected=Status.SENT,
                          reason="customer opened link",
                          audit_data={"ip": _ip(event), "userAgent": _ua(event)})
        status = Status.VIEWED
    p = version["priced"]
    pdf_link = presign(version["pdfKey"], minutes=30)
    head = (f"<h2>Quotation for {escape(quote['clientName'])}</h2>"
            f"<div class='card'><p><b>Total incl. VAT: {escape(str(p['gross_total']))} {escape(p['currency'])}</b>"
            f"<br><span class='muted'>Net {escape(str(p['net_total']))} · {p['term_months']} months · version "
            f"{int(claims['v'])} · valid until {escape(str(quote.get('expiresAt', ''))[:10])}</span></p>"
            f"<p><a href='{escape(pdf_link, quote=True)}'>Download PDF</a></p></div>")
    if status in (Status.VIEWED, Status.SENT):
        t = escape(token, quote=True)
        reasons = "".join(f"<option value='{r}'>{r.replace('_', ' ').title()}</option>" for r in sorted(REJECT_REASONS))
        actions = (f"<div class='card'><h3>Accept</h3><form method='post' action='{quote_id}/accept'>"
                   f"<input type='hidden' name='t' value='{t}'><label>Full name</label><input name='name' required>"
                   f"<label>Email</label><input name='email' type='email' required>"
                   f"<label><input type='checkbox' name='agree' value='yes' required style='width:auto'> I accept this "
                   f"quotation and its terms on behalf of {escape(quote['clientName'])}.</label><br><br>"
                   f"<button>Accept quotation</button></form></div>"
                   f"<div class='card'><h3>Decline</h3><form method='post' action='{quote_id}/decline'>"
                   f"<input type='hidden' name='t' value='{t}'><select name='reasonCode'>{reasons}</select>"
                   f"<button class='secondary'>Decline</button></form></div>")
    else:
        actions = f"<div class='card'><p>This quotation is <b>{escape(status.lower())}</b>.</p></div>"
    return html_response(200, PAGE.format(title="Quotation", body=head + actions))


def public_accept(event, quote_id):
    data = parse_body(event)
    claims, quote, version = _public_context(event, quote_id, data.get("t"))
    name, mail = str(data.get("name", "")).strip()[:120], str(data.get("email", "")).strip()[:200]
    if not name or "@" not in mail or data.get("agree") != "yes":
        raise HttpError(400, "name, email and agreement are required")
    if version.get("supersededBy") or int(quote.get("currentVersion", 0)) != int(claims["v"]):
        raise HttpError(410, "a newer version of this quotation exists")
    acceptance = {"name": name, "email": mail, "ip": _ip(event), "userAgent": _ua(event),
                  "version": int(claims["v"]), "pdfSha256": version["pdfSha256"],
                  "at": datetime.now(UTC).isoformat(timespec="seconds")}
    repo().transition(quote_id, Status.ACCEPTED, actor=CUSTOMER, roles={CUSTOMER}, reason=f"accepted by {name}",
                      set_fields={"acceptance": acceptance}, audit_data=acceptance)
    notify.send_email([mail], "Quotation accepted", notify.simple_html(
        "Thank you", [f"We received your acceptance of quotation {quote_id[:8].upper()} (version {claims['v']})."]))
    notify.send_email(quote.get("createdByEmail"), f"ACCEPTED: {quote['clientName']}",
                      notify.simple_html("Quote accepted", [f"Accepted by {name} <{mail}>.",
                                                            "Please record the actual headcount."]))
    return html_response(200, PAGE.format(title="Accepted", body="<h2>Thank you!</h2><p>Your acceptance has been recorded. "
                                                                  "A confirmation was sent to your email.</p>"))


def public_decline(event, quote_id):
    data = parse_body(event)
    _claims, quote, _version = _public_context(event, quote_id, data.get("t"))
    reason = data.get("reasonCode", "other")
    reason = reason if reason in REJECT_REASONS else "other"
    repo().transition(quote_id, Status.REJECTED, actor=CUSTOMER, roles={CUSTOMER}, reason=f"customer declined: {reason}",
                      set_fields={"rejectReason": reason}, audit_data={"ip": _ip(event)})
    notify.send_email(quote.get("createdByEmail"), f"DECLINED: {quote['clientName']}",
                      notify.simple_html("Quote declined", [f"Reason: {reason}"]))
    return html_response(200, PAGE.format(title="Declined", body="<h2>Thank you for letting us know.</h2>"))


def _ip(event) -> str:
    return event.get("requestContext", {}).get("http", {}).get("sourceIp", "")


def _ua(event) -> str:
    return event.get("requestContext", {}).get("http", {}).get("userAgent", "")[:300]


# ------------------------------------------------------------------ router

AUTH_ROUTES = {
    "POST /quotes": create_quote,
    "GET /quotes": list_quotes,
}
AUTH_ID_ROUTES = {
    "GET /quotes/{id}": get_quote,
    "GET /quotes/{id}/audit": get_audit,
    "POST /quotes/{id}/confirm": confirm,
    "POST /quotes/{id}/revise": revise,
    "POST /quotes/{id}/approve": approve,
    "POST /quotes/{id}/request-changes": request_changes,
    "POST /quotes/{id}/send": send_quote,
    "POST /quotes/{id}/reject": reject,
    "POST /quotes/{id}/cancel": cancel,
    "POST /quotes/{id}/actual-headcount": actual_headcount,
}
PUBLIC_ROUTES = {
    "GET /public/quotes/{id}": public_view,
    "POST /public/quotes/{id}/accept": public_accept,
    "POST /public/quotes/{id}/decline": public_decline,
}


def handler(event, _context):
    route = event.get("routeKey", "")
    quote_id = (event.get("pathParameters") or {}).get("id")
    started = time.time()
    try:
        if route in PUBLIC_ROUTES:
            try:
                return PUBLIC_ROUTES[route](event, quote_id)
            except (HttpError, LifecycleError) as exc:
                status = getattr(exc, "status", None) or getattr(exc, "http_status", 400)
                return html_response(status, PAGE.format(title="Error", body=f"<h2>{escape(str(exc))}</h2>"))
        user, email, groups = identity(event)
        if route in AUTH_ROUTES:
            return AUTH_ROUTES[route](event, user, email, groups)
        if route in AUTH_ID_ROUTES:
            return AUTH_ID_ROUTES[route](event, user, email, groups, quote_id)
        return json_response(404, {"error": f"no route {route}"})
    except HttpError as exc:
        return json_response(exc.status, {"error": str(exc)})
    except NotFound as exc:
        return json_response(404, {"error": str(exc)})
    except LifecycleError as exc:
        return json_response(exc.http_status, {"error": str(exc)})
    except Exception:  # noqa: BLE001
        log.exception("unhandled error on %s", route)
        return json_response(500, {"error": "internal error"})
    finally:
        log.info(json.dumps({"route": route, "quoteId": quote_id, "ms": int((time.time() - started) * 1000)}))
