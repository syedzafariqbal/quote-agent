import json
import os
import sys
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "app" / "src"))

os.environ.setdefault("AWS_DEFAULT_REGION", "eu-central-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ["EVENT_BUS_NAME"] = ""  # no EventBridge calls in unit tests


@pytest.fixture(scope="session")
def catalog():
    from quote_core.catalog import validate_catalog

    raw = (ROOT / "catalog" / "catalog.v2026-10.json").read_text()
    return validate_catalog(json.loads(raw, parse_float=Decimal))


@pytest.fixture
def table():
    """A moto DynamoDB table with the same schema Terraform creates."""
    from moto import mock_aws

    with mock_aws():
        import boto3

        ddb = boto3.client("dynamodb")
        ddb.create_table(
            TableName="quotes-test",
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[{"AttributeName": n, "AttributeType": "S"} for n in ("PK", "SK", "GSI1PK", "GSI1SK")],
            KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
            GlobalSecondaryIndexes=[{
                "IndexName": "GSI1",
                "KeySchema": [{"AttributeName": "GSI1PK", "KeyType": "HASH"},
                              {"AttributeName": "GSI1SK", "KeyType": "RANGE"}],
                "Projection": {"ProjectionType": "ALL"},
            }],
        )
        yield "quotes-test"
