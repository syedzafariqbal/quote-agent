"""HMAC-signed, expiring customer links. The secret lives in SSM Parameter Store (SecureString)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from functools import lru_cache


class InvalidToken(Exception):
    pass


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_token(quote_id: str, version: int, expires_at: int, secret: bytes) -> str:
    payload = _b64(json.dumps({"q": quote_id, "v": version, "e": expires_at}, separators=(",", ":")).encode())
    sig = _b64(hmac.new(secret, payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}"


def verify_token(token: str, quote_id: str, secret: bytes, now: int | None = None) -> dict:
    try:
        payload, sig = token.split(".", 1)
    except (AttributeError, ValueError):
        raise InvalidToken("malformed token") from None
    expected = _b64(hmac.new(secret, payload.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        raise InvalidToken("bad signature")
    data = json.loads(_unb64(payload))
    if data.get("q") != quote_id:
        raise InvalidToken("token is for a different quote")
    if int(data["e"]) < (now or int(time.time())):
        raise InvalidToken("link expired")
    return data


@lru_cache(maxsize=1)
def link_secret(param_name: str) -> bytes:
    import boto3

    value = boto3.client("ssm").get_parameter(Name=param_name, WithDecryption=True)["Parameter"]["Value"]
    return value.encode()
