import pytest

from quote_core.estimator import EstimationError, normalize_company, validate_estimate
from quote_core.signing import InvalidToken, make_token, verify_token

SECRET = b"s3cret"


def test_token_roundtrip():
    tok = make_token("q1", 2, 2_000_000_000, SECRET)
    assert verify_token(tok, "q1", SECRET, now=1_900_000_000)["v"] == 2


@pytest.mark.parametrize("mutate", [
    lambda t: t[:-2] + "xx",
    lambda t: "garbage",
])
def test_token_tamper(mutate):
    tok = make_token("q1", 1, 2_000_000_000, SECRET)
    with pytest.raises(InvalidToken):
        verify_token(mutate(tok), "q1", SECRET, now=1)


def test_token_wrong_quote_and_expiry():
    tok = make_token("q1", 1, 1000, SECRET)
    with pytest.raises(InvalidToken, match="different"):
        verify_token(tok, "q2", SECRET, now=1)
    with pytest.raises(InvalidToken, match="expired"):
        verify_token(tok, "q1", SECRET, now=2000)


def test_normalize():
    assert normalize_company("Müller Logistik GmbH & Co. KG") == "muller logistik"


def test_validate_estimate():
    ok = validate_estimate({"estimate": 10, "min": 5, "max": 20, "confidence": 0.9, "recognised": False})
    assert ok["confidence"] == 0.4  # unrecognised companies are capped
    with pytest.raises(EstimationError):
        validate_estimate({"estimate": 30, "min": 5, "max": 20, "confidence": 0.9})
    with pytest.raises(EstimationError):
        validate_estimate({"estimate": "lots"})
