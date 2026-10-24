"""Invoked by EventBridge Scheduler one-time schedules created when a quote is SENT.

Each firing re-checks the current state, so a quote that was accepted, revised or
cancelled in the meantime is left alone (no need to delete schedules)."""

from __future__ import annotations

import logging

from quote_core import notify
from quote_core.config import settings
from quote_core.lifecycle import SYSTEM, ConcurrentModification, QuoteRepo, Status

log = logging.getLogger()
log.setLevel(logging.INFO)


def handler(event, _context):
    quote_id, version, kind = event["quoteId"], int(event["version"]), event["kind"]
    repo = QuoteRepo(settings().table_name)
    quote = repo.get_quote(quote_id)
    status = quote["status"]
    if status not in (Status.SENT, Status.VIEWED) or int(quote.get("sentVersion", 0)) != version:
        log.info("skip %s for %s: status=%s sentVersion=%s", kind, quote_id, status, quote.get("sentVersion"))
        return {"skipped": True}

    if kind == "expire":
        try:
            repo.transition(quote_id, Status.EXPIRED, actor="scheduler", roles={SYSTEM}, expected=status,
                            reason="validity period ended")
        except ConcurrentModification:
            log.info("quote %s changed concurrently; not expiring", quote_id)
            return {"skipped": True}
        notify.send_email(quote.get("createdByEmail"), f"Quote expired: {quote['clientName']}",
                          notify.simple_html("Quote expired", [f"Quote {quote_id} expired without a decision."]))
        return {"expired": True}

    days = event.get("daysLeft", "?")
    notify.send_email(quote.get("createdByEmail"), f"Reminder: quote for {quote['clientName']} expires in {days} days",
                      notify.simple_html("Follow-up reminder", [
                          f"Status: {status}. The quote expires in {days} days.",
                          "Consider following up with the customer."]))
    return {"reminded": True}
