import hashlib
import mimetypes
from pathlib import Path
from typing import BinaryIO

import boto3
from botocore.client import Config

from .config import get_settings


class Storage:
    def save_upload(self, object_key: str, source: BinaryIO, content_type: str = "") -> tuple[int, str]:
        raise NotImplementedError

    def open(self, object_key: str) -> tuple[BinaryIO, str]:
        raise NotImplementedError

    def delete(self, object_key: str) -> None:
        raise NotImplementedError


class LocalStorage(Storage):
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, object_key: str) -> Path:
        path = (self.root / object_key).resolve()
        if self.root.resolve() not in path.parents:
            raise ValueError("Invalid storage key")
        return path

    def save_upload(self, object_key: str, source: BinaryIO, content_type: str = "") -> tuple[int, str]:
        target = self._path(object_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        size = 0
        with target.open("wb") as output:
            while chunk := source.read(1024 * 1024):
                output.write(chunk)
                digest.update(chunk)
                size += len(chunk)
        return size, digest.hexdigest()

    def open(self, object_key: str) -> tuple[BinaryIO, str]:
        path = self._path(object_key)
        return path.open("rb"), mimetypes.guess_type(path.name)[0] or "application/octet-stream"

    def delete(self, object_key: str) -> None:
        path = self._path(object_key)
        if path.exists():
            path.unlink()


class CosStorage(Storage):
    def __init__(self) -> None:
        settings = get_settings()
        if not settings.cos_secret_id or not settings.cos_secret_key or not settings.cos_bucket:
            raise RuntimeError("COS storage requires COS credentials and bucket configuration")
        self.bucket = settings.cos_bucket
        self.client = boto3.client(
            "s3",
            region_name=settings.cos_region,
            aws_access_key_id=settings.cos_secret_id,
            aws_secret_access_key=settings.cos_secret_key,
            config=Config(signature_version="s3v4"),
            endpoint_url=f"https://cos.{settings.cos_region}.myqcloud.com",
        )

    def save_upload(self, object_key: str, source: BinaryIO, content_type: str = "") -> tuple[int, str]:
        digest = hashlib.sha256()
        size = 0
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
        source.seek(0)
        self.client.put_object(
            Bucket=self.bucket,
            Key=object_key,
            Body=source,
            ContentType=content_type or "application/octet-stream",
        )
        return size, digest.hexdigest()

    def open(self, object_key: str) -> tuple[BinaryIO, str]:
        result = self.client.get_object(Bucket=self.bucket, Key=object_key)
        return result["Body"], result.get("ContentType", "application/octet-stream")

    def delete(self, object_key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=object_key)


def get_storage() -> Storage:
    settings = get_settings()
    if settings.storage_backend.lower() == "cos":
        return CosStorage()
    return LocalStorage(settings.storage_root)
