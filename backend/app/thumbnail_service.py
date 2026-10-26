import secrets
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from .models import AssetFile
from .storage import Storage
from .thumbnails import THUMBNAIL_MIME_TYPE, generate_image_thumbnail
from .video_thumbnails import generate_video_thumbnail


@dataclass(frozen=True)
class ThumbnailAsset:
    asset_file: AssetFile
    source_width: int
    source_height: int
    duration_ms: int | None = None


def _save_thumbnail(
    storage: Storage,
    source_name: str,
    width: int,
    height: int,
    content: BinaryIO,
) -> AssetFile:
    stem = (Path(source_name).stem or "thumbnail")[:180]
    object_key = f"thumbnails/{secrets.token_hex(16)}-preview.webp"
    size, digest = storage.save_upload(object_key, content, THUMBNAIL_MIME_TYPE)
    return AssetFile(
        kind="thumbnail",
        object_key=object_key,
        original_name=f"{stem}-thumbnail.webp",
        mime_type=THUMBNAIL_MIME_TYPE,
        size_bytes=size,
        sha256=digest,
        width=width,
        height=height,
    )


def create_image_thumbnail(
    storage: Storage,
    original: AssetFile,
    source_name: str,
) -> ThumbnailAsset | None:
    stream, _ = storage.open(original.object_key)
    try:
        result = generate_image_thumbnail(stream)
    finally:
        stream.close()
    if not result:
        return None

    return ThumbnailAsset(
        asset_file=_save_thumbnail(
            storage, source_name, result.width, result.height, result.content
        ),
        source_width=result.source_width,
        source_height=result.source_height,
    )


def create_video_thumbnail(
    storage: Storage,
    original: AssetFile,
    source_name: str,
) -> ThumbnailAsset | None:
    suffix = Path(source_name).suffix.lower()
    if len(suffix) < 2 or not suffix[1:].isalnum() or len(suffix) > 16:
        suffix = ".media"

    with tempfile.TemporaryDirectory(prefix="mov-asset-video-") as temp_dir:
        temp_root = Path(temp_dir)
        source_path = temp_root / f"source{suffix}"
        frame_path = temp_root / "frame.png"
        stream, _ = storage.open(original.object_key)
        try:
            with source_path.open("wb") as destination:
                shutil.copyfileobj(stream, destination, length=1024 * 1024)
        finally:
            stream.close()

        result = generate_video_thumbnail(source_path, frame_path)

    if not result:
        return None
    return ThumbnailAsset(
        asset_file=_save_thumbnail(
            storage,
            source_name,
            result.content.width,
            result.content.height,
            result.content.content,
        ),
        source_width=result.source_width,
        source_height=result.source_height,
        duration_ms=result.duration_ms,
    )
