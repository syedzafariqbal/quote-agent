"""DynamoDB Stream consumer: copies every new audit item to the audit bucket
(S3 Object Lock in production), giving a tamper-resistant copy of the trail.
The event source mapping filters to INSERTs whose SK starts with AUDIT#."""

from __future__ import annotations

import json
import os

import boto3
from boto3.dynamodb.types import TypeDeserializer

from quote_core.serde import dumps

s3 = boto3.client("s3")
_des = TypeDeserializer()
BUCKET = os.environ.get("AUDIT_BUCKET", "")


def handler(event, _context):
    failures = []
    for record in event.get("Records", []):
        try:
            image = record["dynamodb"]["NewImage"]
            item = {k: _des.deserialize(v) for k, v in image.items()}
            key = f"audit/{item['quoteId']}/{item['SK'].removeprefix('AUDIT#').replace(':', '-')}.json"
            s3.put_object(Bucket=BUCKET, Key=key, Body=dumps(item).encode(), ContentType="application/json")
        except Exception:  # noqa: BLE001
            failures.append({"itemIdentifier": record["dynamodb"]["SequenceNumber"]})
    return {"batchItemFailures": failures}


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps(handler({"Records": []}, None)))
