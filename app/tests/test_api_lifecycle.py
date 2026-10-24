"""Full lifecycle through the API Lambda handler + worker pipeline, all on moto."""

import json
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

import boto3
import pytest

from tests_support import FakeBedrock


def _event(route, body=None, groups="[SalesRep]", email="rep@example.com", quote_id=None, query=None, form=False):
    headers = {"content-type": "application/x-www-form-urlencoded" if form else "application/json"}
    if form:
        from urllib.parse import urlencode

        raw = urlencode(body or {})
    else:
        raw = json.dumps(body) if body is not None else ""
    return {
        "routeKey": route,
        "pathParameters": {"id": quote_id} if quote_id else None,
        "queryStringParameters": query,
        "headers": headers,
        "body": raw,
        "requestContext": {
            "http": {"sourceIp": "203.0.113.9", "userAgent": "pytest"},
            "authorizer": {"jwt": {"claims": {"cognito:groups": groups, "email": email}}},
        },
    }


@pytest.fixture
def env(table, catalog, monkeypatch):
    s3 = boto3.client("s3")
    s3.create_bucket(Bucket="quotes", CreateBucketConfiguration={"LocationConstraint": "eu-central-1"})
    q = boto3.client("sqs").create_queue(QueueName="requests")["QueueUrl"]
    boto3.client("ssm").put_parameter(Name="/test/link", Value="secret", Type="SecureString")
    boto3.client("scheduler").create_schedule_group(Name="grp")
    for k, v in {"TABLE_NAME": table, "QUEUE_URL": q, "QUOTES_BUCKET": "quotes", "SCHEDULER_GROUP": "grp",
                 "SCHEDULER_TARGET_ARN": "arn:aws:lambda:eu-central-1:123456789012:function:x",
                 "SCHEDULER_ROLE_ARN": "arn:aws:iam::123456789012:role/x", "LINK_SECRET_PARAM": "/test/link",
                 "PUBLIC_BASE_URL": "https://api.example.com"}.items():
        monkeypatch.setenv(k, v)
    from handlers import api
    from quote_core import config, signing

    config.settings.cache_clear()
    signing.link_secret.cache_clear()
    api.repo.cache_clear()
    api.clients.cache_clear()

    from quote_core.estimator import EstimateCache
    from quote_core.lifecycle import QuoteRepo
    from worker.pipeline import Deps

    repo = QuoteRepo(table)
    deps = Deps(repo=repo, cache=EstimateCache(repo.table), s3=s3, quotes_bucket="quotes",
                catalog_loader=lambda: catalog, estimator_model_id="m", writer_model_id="",
                confidence_threshold=0.7, resolve_company=lambda *a, **k: None,
                route_distance=lambda *a: None, render=lambda **kw: (b"%PDF", "sha"),
                bedrock=FakeBedrock())
    yield api, deps, q


def _drain(deps, queue_url):
    from worker.pipeline import handle_message

    sqs = boto3.client("sqs")
    for m in sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=10).get("Messages", []):
        handle_message(json.loads(m["Body"]), deps)
        sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=m["ReceiptHandle"])


def test_full_lifecycle_with_approval_and_acceptance(env):
    api, deps, q = env
    r = api.handler(_event("POST /quotes", {"clientName": "Acme Logistics GmbH", "discountPct": 15,
                                            "customerEmail": "buyer@example.com"}), None)
    assert r["statusCode"] == 202
    qid = json.loads(r["body"])["quoteId"]
    _drain(deps, q)

    body = json.loads(api.handler(_event("GET /quotes/{id}", quote_id=qid), None)["body"])
    assert body["quote"]["status"] == "PENDING_APPROVAL" and "pdfUrl" in body

    # rep cannot send or approve
    assert api.handler(_event("POST /quotes/{id}/send", {}, quote_id=qid), None)["statusCode"] == 409
    assert api.handler(_event("POST /quotes/{id}/approve", {}, quote_id=qid), None)["statusCode"] == 403
    # manager approves
    r = api.handler(_event("POST /quotes/{id}/approve", {"comment": "ok"}, groups="[Manager]",
                           email="boss@example.com", quote_id=qid), None)
    assert r["statusCode"] == 200

    r = api.handler(_event("POST /quotes/{id}/send", {}, quote_id=qid), None)
    assert r["statusCode"] == 200
    link = json.loads(r["body"])["customerLink"]
    token = parse_qs(urlparse(link).query)["t"][0]
    schedules = boto3.client("scheduler").list_schedules(GroupName="grp")["Schedules"]
    assert len(schedules) == 3  # 2 reminders + expiry

    page = api.handler(_event("GET /public/quotes/{id}", quote_id=qid, query={"t": token}), None)
    assert page["statusCode"] == 200 and "Accept quotation" in page["body"]
    assert deps.repo.get_quote(qid)["status"] == "VIEWED"

    bad = api.handler(_event("GET /public/quotes/{id}", quote_id=qid, query={"t": token + "x"}), None)
    assert bad["statusCode"] == 403

    r = api.handler(_event("POST /public/quotes/{id}/accept", {"t": token, "name": "Jane Buyer",
                                                               "email": "jane@example.com", "agree": "yes"},
                           quote_id=qid, form=True), None)
    assert r["statusCode"] == 200
    quote = deps.repo.get_quote(qid)
    assert quote["status"] == "ACCEPTED" and quote["acceptance"]["pdfSha256"] == "sha"

    r = api.handler(_event("POST /quotes/{id}/actual-headcount", {"employees": 150}, quote_id=qid), None)
    assert r["statusCode"] == 200
    audit = json.loads(api.handler(_event("GET /quotes/{id}/audit", quote_id=qid), None)["body"])
    assert [a["to"] for a in audit] == ["REQUESTED", "PROCESSING", "PENDING_APPROVAL", "APPROVED", "SENT",
                                        "VIEWED", "ACCEPTED"]


def test_revision_after_send_invalidates_old_link(env):
    api, deps, q = env
    qid = json.loads(api.handler(_event("POST /quotes", {"clientName": "Acme", "employees": 20}), None)["body"])["quoteId"]
    _drain(deps, q)
    link = json.loads(api.handler(_event("POST /quotes/{id}/send", {"customerEmail": "b@example.com"},
                                         quote_id=qid), None)["body"])["customerLink"]
    token = parse_qs(urlparse(link).query)["t"][0]
    assert api.handler(_event("POST /quotes/{id}/revise", {"termMonths": 36}, quote_id=qid), None)["statusCode"] == 202
    _drain(deps, q)
    assert deps.repo.get_quote(qid)["currentVersion"] == 2
    old = api.handler(_event("GET /public/quotes/{id}", quote_id=qid, query={"t": token}), None)
    assert old["statusCode"] == 410
    v2 = deps.repo.get_version(qid, 2)
    assert v2["priced"]["term_discount_pct"] == Decimal("10.00") or v2["priced"]["term_discount_pct"] == Decimal("10")


def test_validation_errors(env):
    api, _, _ = env
    assert api.handler(_event("POST /quotes", {"clientName": ""}), None)["statusCode"] == 400
    assert api.handler(_event("POST /quotes", {"clientName": "Acme", "language": "fr"}), None)["statusCode"] == 400
    assert api.handler(_event("POST /quotes", {"clientName": "Acme"}, groups="[Manager]"), None)["statusCode"] == 403
    assert api.handler(_event("GET /quotes/{id}", quote_id="nope"), None)["statusCode"] == 404
