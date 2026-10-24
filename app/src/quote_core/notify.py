"""Email notifications via Amazon SES v2. Silently skipped when SENDER_EMAIL is empty.

SES starts in sandbox mode: both sender and recipients must be verified identities
until you request production access.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from html import escape

from .config import settings

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _client():
    import boto3

    return boto3.client("sesv2")


def send_email(to: str | list[str] | None, subject: str, html_body: str) -> bool:
    sender = settings().sender_email
    recipients = [to] if isinstance(to, str) else [t for t in (to or []) if t]
    if not sender or not recipients:
        log.info("email skipped (sender=%r, to=%r): %s", sender, recipients, subject)
        return False
    try:
        _client().send_email(
            FromEmailAddress=sender,
            Destination={"ToAddresses": recipients},
            Content={"Simple": {"Subject": {"Data": subject}, "Body": {"Html": {"Data": html_body}}}},
        )
        return True
    except Exception:  # noqa: BLE001
        log.exception("failed to send email %r", subject)
        return False


def simple_html(title: str, lines: list[str], link: str | None = None, link_text: str = "Open") -> str:
    body = "".join(f"<p>{escape(line)}</p>" for line in lines)
    button = (f'<p><a href="{escape(link, quote=True)}" style="background:#1f4e79;color:#fff;'
              f'padding:10px 16px;text-decoration:none;border-radius:4px">{escape(link_text)}</a></p>') if link else ""
    return f"<html><body style='font-family:Arial,sans-serif'><h2>{escape(title)}</h2>{body}{button}</body></html>"
