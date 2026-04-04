from __future__ import annotations

import logging
from pathlib import Path

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)


def _s3_key(prefix: str, bucket_label: str, path: str) -> str:
    """Build the full S3 object key from the configured prefix, logical bucket label, and path."""
    parts = [p for p in [prefix, bucket_label, path.strip("/")] if p]
    return "/".join(parts)


class S3StorageBucket:
    """AWS S3-backed storage bucket. Stores objects under ``<prefix>/<bucket_label>/<path>``."""

    def __init__(self, bucket_label: str, settings: Settings):
        import boto3  # imported lazily so local-fallback dev environments don't need boto3
        from botocore.config import Config

        self.bucket_label = bucket_label
        self.settings = settings
        self._s3 = boto3.client(
            "s3",
            region_name=settings.AWS_S3_REGION,
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            config=Config(
                connect_timeout=10,
                read_timeout=60,
                retries={"max_attempts": 2, "mode": "standard"},
            ),
        )
        self._bucket_name = settings.AWS_S3_BUCKET_NAME
        self._prefix = settings.AWS_S3_REPORTS_PREFIX

    def _key(self, path: str) -> str:
        return _s3_key(self._prefix, self.bucket_label, path)

    def upload(self, path: str, payload: bytes, options: dict | None = None) -> None:
        key = self._key(path)
        extra: dict = {}
        if options:
            if ct := options.get("content-type") or options.get("ContentType"):
                extra["ContentType"] = ct
        logger.debug("S3 upload: bucket=%s key=%s bytes=%s", self._bucket_name, key, len(payload))
        self._s3.put_object(Bucket=self._bucket_name, Key=key, Body=payload, **extra)

    def download(self, path: str) -> bytes:
        key = self._key(path)
        logger.debug("S3 download: bucket=%s key=%s", self._bucket_name, key)
        response = self._s3.get_object(Bucket=self._bucket_name, Key=key)
        return response["Body"].read()

    def get_s3_key(self, path: str) -> str:
        """Return the raw S3 object key for ``path``. Store this in the DB instead of a URL."""
        return self._key(path)

    def get_public_url(self, path: str) -> str:
        """
        Generate a fresh presigned GET URL valid for ``AWS_S3_PRESIGNED_URL_EXPIRY`` seconds.

        Call this at *serve time* (not at upload time) so the URL never goes stale in the DB.
        Store ``get_s3_key(path)`` in the DB and call this when building API responses.
        """
        key = self._key(path)
        url = self._s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket_name, "Key": key},
            ExpiresIn=self.settings.AWS_S3_PRESIGNED_URL_EXPIRY,
        )
        logger.debug(
            "S3 presigned URL generated: bucket=%s key=%s expiry=%ss",
            self._bucket_name,
            key,
            self.settings.AWS_S3_PRESIGNED_URL_EXPIRY,
        )
        return url

    def get_file_size(self, path: str) -> int | None:
        """Return object size in bytes via HeadObject, or None if not found."""
        key = self._key(path)
        try:
            response = self._s3.head_object(Bucket=self._bucket_name, Key=key)
            return response["ContentLength"]
        except Exception:
            return None


class _LocalStorageBucket:
    """Local filesystem fallback used when AWS credentials are not configured (dev/test)."""

    def __init__(self, bucket: str, settings: Settings):
        self.bucket = bucket
        self.settings = settings
        self.root = Path(settings.STORAGE_ROOT) / bucket
        self.root.mkdir(parents=True, exist_ok=True)

    def upload(self, path: str, payload: bytes, _options: dict | None = None) -> None:
        target = self._safe_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)

    def download(self, path: str) -> bytes:
        target = self._safe_path(path)
        return target.read_bytes()

    def get_s3_key(self, path: str) -> str:
        """In local mode, the 'key' is just the relative path — stored in the DB the same way."""
        return path.strip("/")

    def get_public_url(self, path: str) -> str:
        normalized = path.strip("/")
        return f"{self.settings.BACKEND_URL}/api/storage/{self.bucket}/{normalized}"

    def get_file_size(self, path: str) -> int | None:
        try:
            return self._safe_path(path).stat().st_size
        except FileNotFoundError:
            return None

    def _safe_path(self, relative_path: str) -> Path:
        candidate = (self.root / relative_path).resolve()
        if not str(candidate).startswith(str(self.root.resolve())):
            raise ValueError("Invalid storage path")
        return candidate


# Union type for type hints across the codebase
StorageBucket = S3StorageBucket | _LocalStorageBucket


class StorageClient:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._use_s3 = bool(
            self.settings.AWS_S3_BUCKET_NAME
            and self.settings.AWS_ACCESS_KEY_ID
            and self.settings.AWS_SECRET_ACCESS_KEY
        )
        if self._use_s3:
            logger.debug("StorageClient: using S3 backend (bucket=%s)", self.settings.AWS_S3_BUCKET_NAME)
        else:
            logger.debug("StorageClient: using local filesystem fallback (root=%s)", self.settings.STORAGE_ROOT)

    def from_(self, bucket: str) -> S3StorageBucket | _LocalStorageBucket:
        if self._use_s3:
            return S3StorageBucket(bucket, self.settings)
        return _LocalStorageBucket(bucket, self.settings)
