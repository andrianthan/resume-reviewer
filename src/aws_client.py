"""S3 + Lambda client used by the Discord bot.

Uploads PDF to S3, invokes the resume-reviewer API Gateway endpoint,
returns the Review JSON. Falls back to local evaluate() if the API is
unreachable (useful for dev / offline testing).
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Any

import boto3
import requests

log = logging.getLogger("resume-reviewer.aws")

LAMBDA_API_URL = os.environ.get("RESUME_API_URL", "")  # https://...amazonaws.com/prod/review
PDF_BUCKET = os.environ.get("RESUME_PDF_BUCKET", "")


def _s3_client():
    return boto3.client("s3")


def upload_pdf(pdf_bytes: bytes, user_id: int) -> str:
    """Upload PDF to S3, return the object key. Bucket deletes objects after 1 day."""
    key = f"uploads/{user_id}/{uuid.uuid4()}.pdf"
    _s3_client().put_object(
        Bucket=PDF_BUCKET,
        Key=key,
        Body=pdf_bytes,
        ContentType="application/pdf",
        ServerSideEncryption="AES256",
    )
    return key


def review_via_api(
    s3_key: str, major: str, class_year: str, user_id: int
) -> dict[str, Any]:
    """POST to API Gateway -> Lambda -> returns Review JSON."""
    payload = {
        "s3_bucket": PDF_BUCKET,
        "s3_key": s3_key,
        "major": major,
        "class_year": class_year,
        "user_id": user_id,
    }
    r = requests.post(LAMBDA_API_URL, json=payload, timeout=55)
    r.raise_for_status()
    return r.json()
