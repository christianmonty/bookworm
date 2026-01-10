# source/gcs.py
from __future__ import annotations

import os
from typing import Tuple
from urllib.parse import urlparse

from google.cloud import storage

ALLOWED_IMAGE_TYPES = {"cover", "copyright"}


def _project() -> str | None:
    return os.environ.get("GOOGLE_CLOUD_PROJECT") or None


def bucket_name() -> str:
    bucket = os.environ.get("GCS_BUCKET")
    if not bucket:
        raise RuntimeError("GCS_BUCKET env var is not set")
    return bucket


def object_key(book_id: int, image_type: str) -> str:
    # REQUIRED format
    return f"raw/{book_id}/{image_type}.jpg"


def gs_uri(bucket: str, key: str) -> str:
    return f"gs://{bucket}/{key}"


def parse_gs_uri(uri: str) -> Tuple[str, str]:
    if not uri.startswith("gs://"):
        raise ValueError(f"Not a gs:// uri: {uri}")
    p = urlparse(uri)
    b = p.netloc
    k = p.path.lstrip("/")
    if not b or not k:
        raise ValueError(f"Invalid gs:// uri: {uri}")
    return b, k


def upload_image(book_id: int, image_type: str, file_bytes: bytes, *, overwrite: bool = True) -> str:
    """
    Upload bytes to GCS at raw/{book_id}/{image_type}.jpg
    Returns the canonical gs://... storage_path.
    """
    if not file_bytes:
        raise ValueError("file_bytes cannot be empty")
    if image_type not in ALLOWED_IMAGE_TYPES:
        raise ValueError("image_type must be 'cover' or 'copyright'")

    bkt = bucket_name()
    key = object_key(book_id, image_type)

    client = storage.Client(project=_project())
    blob = client.bucket(bkt).blob(key)

    if not overwrite and blob.exists():
        return gs_uri(bkt, key)

    blob.upload_from_string(file_bytes, content_type="image/jpeg")
    return gs_uri(bkt, key)


def read_bytes(storage_path: str) -> bytes:
    """
    Read bytes from gs://... only (GCS-first). If someone left a local path in DB, fail loudly.
    """
    if not storage_path.startswith("gs://"):
        raise ValueError(f"Expected gs://... storage_path, got: {storage_path}")

    bkt, key = parse_gs_uri(storage_path)
    client = storage.Client(project=_project())
    blob = client.bucket(bkt).blob(key)
    return blob.download_as_bytes()
