import secrets
from pathlib import Path

from .models import AssetFile
from .storage import Storage
from .thumbnails import THUMBNAIL_MIME_TYPE, generate_image_thumbnail


def create_image_thumbnail(
    storage: Storage,
    original: AssetFile,
    source_name: str,
) -> tuple[AssetFile, int, int] | None:
    stream, _ = storage.open(original.object_key)
    try:
        result = generate_image_thumbnail(stream)
    finally:
        stream.close()
    if not result:
        return None

    stem = (Path(source_name).stem or "thumbnail")[:180]
    object_key = f"thumbnails/{secrets.token_hex(16)}-preview.webp"
    size, digest = storage.save_upload(object_key, result.content, THUMBNAIL_MIME_TYPE)
    thumbnail = AssetFile(
        kind="thumbnail",
        object_key=object_key,
        original_name=f"{stem}-thumbnail.webp",
        mime_type=THUMBNAIL_MIME_TYPE,
        size_bytes=size,
        sha256=digest,
        width=result.width,
        height=result.height,
    )
    return thumbnail, result.source_width, result.source_height
