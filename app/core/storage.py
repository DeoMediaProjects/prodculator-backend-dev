from __future__ import annotations

from pathlib import Path

from app.core.config import Settings, get_settings


class StorageBucket:
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

    def get_public_url(self, path: str) -> str:
        normalized = path.strip("/")
        return f"{self.settings.BACKEND_URL}/api/storage/{self.bucket}/{normalized}"

    def _safe_path(self, relative_path: str) -> Path:
        candidate = (self.root / relative_path).resolve()
        if not str(candidate).startswith(str(self.root.resolve())):
            raise ValueError("Invalid storage path")
        return candidate


class StorageClient:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def from_(self, bucket: str) -> StorageBucket:
        return StorageBucket(bucket, self.settings)
