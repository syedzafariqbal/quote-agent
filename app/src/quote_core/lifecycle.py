"""Quote lifecycle: the state machine and its DynamoDB persistence.

Every status change goes through `QuoteRepo.transition()`, which:
  1. checks the (from, to) pair and the actor's role against TRANSITIONS,
  2. writes the new status with a conditional update (status must still equal `from`),
  3. writes an append-only audit item in the same transaction,
  4. publishes a `Quote<Status>` event on EventBridge.
Concurrent clicks or SQS redeliveries therefore cannot corrupt state.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from . import events


class Status(StrEnum):
    REQUESTED = "REQUESTED"
    PROCESSING = "PROCESSING"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    SENT = "SENT"
    VIEWED = "VIEWED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


S = Status
SYSTEM = "system"
CUSTOMER = "customer"
REP = frozenset({"SalesRep", "Admin"})
APPROVERS = frozenset({"Manager", "Finance", "Admin"})

TERMINAL = frozenset({S.ACCEPTED, S.REJECTED, S.EXPIRED, S.CANCELLED})
REVISABLE = (S.NEEDS_REVIEW, S.DRAFT, S.PENDING_APPROVAL, S.APPROVED, S.SENT, S.VIEWED, S.FAILED)

# (from, to) -> roles allowed to perform it
TRANSITIONS: dict[tuple[Status, Status], frozenset[str]] = {
    (S.REQUESTED, S.PROCESSING): frozenset({SYSTEM}),
    (S.PROCESSING, S.NEEDS_REVIEW): frozenset({SYSTEM}),
    (S.PROCESSING, S.DRAFT): frozenset({SYSTEM}),
    (S.PROCESSING, S.PENDING_APPROVAL): frozenset({SYSTEM}),
    (S.PROCESSING, S.FAILED): frozenset({SYSTEM}),
    (S.PENDING_APPROVAL, S.APPROVED): APPROVERS,
    (S.PENDING_APPROVAL, S.DRAFT): APPROVERS,  # changes requested
    (S.DRAFT, S.SENT): REP,  # only when no approval is required (guarded in the API)
    (S.APPROVED, S.SENT): REP,
    (S.SENT, S.VIEWED): frozenset({CUSTOMER}),
    (S.SENT, S.ACCEPTED): frozenset({CUSTOMER}),
    (S.VIEWED, S.ACCEPTED): frozenset({CUSTOMER}),
    (S.SENT, S.REJECTED): REP | {CUSTOMER},
    (S.VIEWED, S.REJECTED): REP | {CUSTOMER},
    (S.SENT, S.EXPIRED): frozenset({SYSTEM}),
    (S.VIEWED, S.EXPIRED): frozenset({SYSTEM}),
}
# confirm headcount / revise / retry: back to PROCESSING for a re-price (new version)
for _src in REVISABLE:
    TRANSITIONS[(_src, S.PROCESSING)] = REP
# cancel from any open state
for _src in Status:
    if _src not in TERMINAL:
        TRANSITIONS.setdefault((_src, S.CANCELLED), REP)


class LifecycleError(Exception):
    """Base class; `http_status` lets the API map errors to responses."""

    http_status = 400


class InvalidTransition(LifecycleError):
    http_status = 409


class Forbidden(LifecycleError):
    http_status = 403


class ConcurrentModification(LifecycleError):
    http_status = 409


class NotFound(LifecycleError):
    http_status = 404


def check_transition(current: Status | str, target: Status | str, roles: set[str] | frozenset[str]) -> None:
    current, target = Status(current), Status(target)
    allowed = TRANSITIONS.get((current, target))
    if allowed is None:
        raise InvalidTransition(f"cannot move a quote from {current} to {target}")
    if not (set(roles) & allowed):
        raise Forbidden(f"{current} -> {target} requires one of {sorted(allowed)}")


def allowed_targets(current: Status | str, roles: set[str]) -> list[str]:
    current = Status(current)
    return sorted(t for (f, t), r in TRANSITIONS.items() if f == current and set(roles) & r)


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# ---------------------------------------------------------------- persistence

def quote_pk(quote_id: str) -> str:
    return f"QUOTE#{quote_id}"


def version_sk(n: int) -> str:
    return f"V#{n:03d}"


class QuoteRepo:
    """Single-table design.

    PK=QUOTE#<id>  SK=META                 status, request, research, currentVersion, ...
    PK=QUOTE#<id>  SK=V#001                immutable priced version (+ pdfKey, pdfSha256)
    PK=QUOTE#<id>  SK=AUDIT#<ts>#<uuid>    append-only audit entries
    PK=IDEMP#<key> SK=IDEMP                idempotency record (ttl)
    PK=COMPANY#<n> SK=ESTIMATE             headcount cache / actuals (ttl)
    GSI1: GSI1PK=STATUS#<status>, GSI1SK=<updatedAt or expiresAt>
    """

    def __init__(self, table_name: str, client: Any | None = None, resource: Any | None = None):
        import boto3

        self.table_name = table_name
        self.client = client or boto3.client("dynamodb")
        self.table = (resource or boto3.resource("dynamodb")).Table(table_name)
        from boto3.dynamodb.types import TypeSerializer

        self._ser = TypeSerializer()

    # -- helpers
    def _av(self, value: Any) -> dict:
        return self._ser.serialize(value)

    def _audit_put(self, quote_id: str, actor: str, frm: str | None, to: str, reason: str | None,
                   data: dict | None = None) -> dict:
        ts = datetime.now(UTC).isoformat(timespec="microseconds")  # sortable, sub-second order
        item = {
            "PK": quote_pk(quote_id),
            "SK": f"AUDIT#{ts}#{uuid.uuid4().hex[:8]}",
            "type": "audit",
            "quoteId": quote_id,
            "at": ts,
            "actor": actor,
            "from": frm or "-",
            "to": to,
        }
        if reason:
            item["reason"] = reason
        if data:
            item["data"] = data
        return {"Put": {"TableName": self.table_name, "Item": {k: self._av(v) for k, v in item.items()},
                        "ConditionExpression": "attribute_not_exists(PK)"}}

    # -- reads
    def get_quote(self, quote_id: str) -> dict:
        item = self.table.get_item(Key={"PK": quote_pk(quote_id), "SK": "META"}, ConsistentRead=True).get("Item")
        if not item:
            raise NotFound(f"quote {quote_id} not found")
        return item

    def get_version(self, quote_id: str, n: int) -> dict | None:
        return self.table.get_item(Key={"PK": quote_pk(quote_id), "SK": version_sk(n)}).get("Item")

    def _query_prefix(self, quote_id: str, prefix: str) -> list[dict]:
        from boto3.dynamodb.conditions import Key

        resp = self.table.query(
            KeyConditionExpression=Key("PK").eq(quote_pk(quote_id)) & Key("SK").begins_with(prefix)
        )
        return resp.get("Items", [])

    def list_versions(self, quote_id: str) -> list[dict]:
        return self._query_prefix(quote_id, "V#")

    def list_audit(self, quote_id: str) -> list[dict]:
        return self._query_prefix(quote_id, "AUDIT#")

    def list_by_status(self, status: str, limit: int = 50) -> list[dict]:
        from boto3.dynamodb.conditions import Key

        resp = self.table.query(
            IndexName="GSI1",
            KeyConditionExpression=Key("GSI1PK").eq(f"STATUS#{Status(status)}"),
            ScanIndexForward=False,
            Limit=limit,
        )
        return resp.get("Items", [])

    # -- writes
    def create_quote(self, quote_id: str, client_name: str, request: dict, actor: str,
                     actor_email: str | None = None, idempotency_key: str | None = None) -> str:
        """Create a quote in REQUESTED. Returns the quote id (existing one on idempotent replay)."""
        ts = now_iso()
        meta = {
            "PK": quote_pk(quote_id), "SK": "META", "type": "quote", "quoteId": quote_id,
            "clientName": client_name, "status": S.REQUESTED.value, "request": request,
            "createdBy": actor, "createdAt": ts, "updatedAt": ts, "currentVersion": 0,
            "GSI1PK": f"STATUS#{S.REQUESTED}", "GSI1SK": ts,
        }
        if actor_email:
            meta["createdByEmail"] = actor_email
        items = [
            {"Put": {"TableName": self.table_name, "Item": {k: self._av(v) for k, v in meta.items()},
                     "ConditionExpression": "attribute_not_exists(PK)"}},
            self._audit_put(quote_id, actor, None, S.REQUESTED, "created"),
        ]
        if idempotency_key:
            idem = {"PK": f"IDEMP#{idempotency_key}", "SK": "IDEMP", "quoteId": quote_id,
                    "ttl": int(time.time()) + 86400}
            items.append({"Put": {"TableName": self.table_name, "Item": {k: self._av(v) for k, v in idem.items()},
                                  "ConditionExpression": "attribute_not_exists(PK)"}})
        try:
            self.client.transact_write_items(TransactItems=items)
        except self.client.exceptions.TransactionCanceledException:
            if idempotency_key:
                existing = self.table.get_item(Key={"PK": f"IDEMP#{idempotency_key}", "SK": "IDEMP"}).get("Item")
                if existing:
                    return existing["quoteId"]
            raise ConcurrentModification("quote already exists") from None
        events.publish("QuoteRequested", {"quoteId": quote_id, "clientName": client_name, "actor": actor})
        return quote_id

    def transition(self, quote_id: str, target: Status | str, *, actor: str, roles: set[str] | frozenset[str],
                   reason: str | None = None, expected: Status | str | None = None,
                   set_fields: dict[str, Any] | None = None, extra_items: list[dict] | None = None,
                   audit_data: dict | None = None) -> dict:
        """Move a quote to `target`. Returns the updated META item."""
        target = Status(target)
        current = Status(expected) if expected else Status(self.get_quote(quote_id)["status"])
        check_transition(current, target, roles)

        ts = now_iso()
        fields: dict[str, Any] = {"status": target.value, "updatedAt": ts, "GSI1PK": f"STATUS#{target}",
                                  "GSI1SK": ts}
        fields.update(set_fields or {})
        names, values, sets = {"#cur": "status"}, {":from": self._av(current.value)}, []
        for i, (k, v) in enumerate(fields.items()):
            names[f"#f{i}"] = k
            values[f":v{i}"] = self._av(v)
            sets.append(f"#f{i} = :v{i}")
        update = {"Update": {
            "TableName": self.table_name,
            "Key": {"PK": self._av(quote_pk(quote_id)), "SK": self._av("META")},
            "UpdateExpression": "SET " + ", ".join(sets),
            "ConditionExpression": "#cur = :from",
            "ExpressionAttributeNames": names,
            "ExpressionAttributeValues": values,
        }}
        items = [update, self._audit_put(quote_id, actor, current, target, reason, audit_data)]
        items.extend(extra_items or [])
        try:
            self.client.transact_write_items(TransactItems=items)
        except self.client.exceptions.TransactionCanceledException as exc:
            raise ConcurrentModification(
                f"quote {quote_id} is no longer {current} (concurrent update or retry)"
            ) from exc

        events.publish(f"Quote{target.value.title().replace('_', '')}",
                       {"quoteId": quote_id, "from": current.value, "to": target.value, "actor": actor,
                        "reason": reason})
        return self.get_quote(quote_id)

    def version_put_item(self, quote_id: str, n: int, payload: dict) -> dict:
        item = {"PK": quote_pk(quote_id), "SK": version_sk(n), "type": "version", "version": n,
                "createdAt": now_iso(), **payload}
        return {"Put": {"TableName": self.table_name, "Item": {k: self._av(v) for k, v in item.items()},
                        "ConditionExpression": "attribute_not_exists(PK)"}}

    def supersede_item(self, quote_id: str, old: int, new: int) -> dict:
        return {"Update": {
            "TableName": self.table_name,
            "Key": {"PK": self._av(quote_pk(quote_id)), "SK": self._av(version_sk(old))},
            "UpdateExpression": "SET supersededBy = :n",
            "ConditionExpression": "attribute_exists(PK)",
            "ExpressionAttributeValues": {":n": self._av(new)},
        }}

    def update_fields(self, quote_id: str, fields: dict[str, Any]) -> None:
        """Non-status metadata update (e.g. recording the actual headcount after acceptance)."""
        names = {f"#f{i}": k for i, k in enumerate(fields)}
        values = {f":v{i}": v for i, v in enumerate(fields.values())}
        self.table.update_item(
            Key={"PK": quote_pk(quote_id), "SK": "META"},
            UpdateExpression="SET " + ", ".join(f"#f{i} = :v{i}" for i in range(len(fields))),
            ConditionExpression="attribute_exists(PK)",
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )
