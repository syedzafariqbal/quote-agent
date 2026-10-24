"""End-to-end worker pipeline against moto, with Bedrock, Location and PDF rendering stubbed."""

from decimal import Decimal

import boto3
import pytest

from quote_core.estimator import Estimate, EstimateCache
from quote_core.lifecycle import QuoteRepo, Status
from tests_support import FakeBedrock
from worker.pipeline import Deps, handle_message


@pytest.fixture
def deps(table, catalog):
    s3 = boto3.client("s3")
    s3.create_bucket(Bucket="quotes", CreateBucketConfiguration={"LocationConstraint": "eu-central-1"})
    repo = QuoteRepo(table)
    return Deps(
        repo=repo, cache=EstimateCache(repo.table), s3=s3, quotes_bucket="quotes",
        catalog_loader=lambda: catalog, estimator_model_id="test-model", writer_model_id="test-small",
        confidence_threshold=0.7,
        resolve_company=lambda name, country=None: {"title": name, "address": "Bremen, DE", "country": "DE",
                                                    "position": [Decimal("8.8"), Decimal("53.08")],
                                                    "categories": [], "ambiguous": False},
        route_distance=lambda pos, offices: ("Bremen", Decimal("10")),
        render=lambda **kw: (b"%PDF-fake", "abc123"),
        bedrock=FakeBedrock(),
    )


def test_create_to_draft(deps):
    deps.repo.create_quote("q1", "Acme Logistics GmbH", {"country": "DE"}, "rep@example.com")
    assert handle_message({"type": "create", "quoteId": "q1"}, deps) == Status.DRAFT
    quote = deps.repo.get_quote("q1")
    assert quote["currentVersion"] == 1 and quote["approvalLevel"] == "none"
    v1 = deps.repo.get_version("q1", 1)
    assert v1["priced"]["tier"] == "Mid" and v1["pdfSha256"] == "abc123"
    assert deps.s3.get_object(Bucket="quotes", Key=v1["pdfKey"])["Body"].read() == b"%PDF-fake"


def test_low_confidence_goes_to_review_then_confirm(deps):
    deps.bedrock = FakeBedrock(confidence=0.3, recognised=False)
    deps.repo.create_quote("q2", "Unknown Startup UG", {}, "rep")
    assert handle_message({"type": "create", "quoteId": "q2"}, deps) == Status.NEEDS_REVIEW
    assert deps.repo.get_quote("q2")["currentVersion"] == 0
    # rep confirms 30 employees (what the API's /confirm does)
    deps.repo.transition("q2", Status.PROCESSING, actor="rep", roles={"SalesRep"},
                         set_fields={"request": {"employees": 30}})
    assert handle_message({"type": "reprice", "quoteId": "q2"}, deps) == Status.DRAFT
    v1 = deps.repo.get_version("q2", 1)
    assert v1["priced"]["employees"] == 30 and v1["estimate"]["source"] == "confirmed"


def test_big_discount_needs_approval_and_revision_supersedes(deps):
    deps.repo.create_quote("q3", "Acme", {"discountPct": Decimal("15")}, "rep")
    assert handle_message({"type": "create", "quoteId": "q3"}, deps) == Status.PENDING_APPROVAL
    deps.repo.transition("q3", Status.PROCESSING, actor="rep", roles={"SalesRep"},
                         set_fields={"request": {"discountPct": Decimal("5")}})
    assert handle_message({"type": "reprice", "quoteId": "q3"}, deps) == Status.DRAFT
    assert deps.repo.get_version("q3", 1)["supersededBy"] == 2


def test_duplicate_message_is_ignored(deps):
    deps.repo.create_quote("q4", "Acme", {}, "rep")
    handle_message({"type": "create", "quoteId": "q4"}, deps)
    calls = deps.bedrock.calls
    assert handle_message({"type": "create", "quoteId": "q4"}, deps) == Status.DRAFT
    assert deps.bedrock.calls == calls  # no second LLM call, no second version
    assert deps.repo.get_quote("q4")["currentVersion"] == 1


def test_cache_and_feedback_loop(deps):
    deps.repo.create_quote("q5", "Acme", {}, "rep")
    handle_message({"type": "create", "quoteId": "q5"}, deps)
    cached = deps.cache.get("ACME gmbh")  # normalisation strips legal suffix/case
    assert cached and cached.source == "cache"
    deps.cache.record_actual("Acme", 140, "q5", Estimate(120, 80, 200, 0.85, "llm"))
    assert deps.cache.get("Acme").source == "actual"
