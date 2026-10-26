import warnings
from dataclasses import dataclass
from io import BytesIO
from typing import BinaryIO

from PIL import Image, ImageOps, UnidentifiedImageError

THUMBNAIL_MAX_EDGE = 720
THUMBNAIL_MIME_TYPE = "image/webp"
THUMBNAIL_QUALITY = 84


@dataclass(frozen=True)
class ThumbnailResult:
    content: BytesIO
    source_width: int
    source_height: int
    width: int
    height: int


def generate_image_thumbnail(source: BinaryIO) -> ThumbnailResult | None:
    """Create a bounded WebP preview without trusting the uploaded MIME type."""
    source.seek(0)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(source) as opened:
                opened.load()
                image = ImageOps.exif_transpose(opened)
                source_width, source_height = image.size
                image.thumbnail(
                    (THUMBNAIL_MAX_EDGE, THUMBNAIL_MAX_EDGE),
                    Image.Resampling.LANCZOS,
                    reducing_gap=3.0,
                )
                has_alpha = image.mode in {"RGBA", "LA"} or (
                    image.mode == "P" and "transparency" in image.info
                )
                image = image.convert("RGBA" if has_alpha else "RGB")
                output = BytesIO()
                image.save(output, "WEBP", quality=THUMBNAIL_QUALITY, method=6)
                width, height = image.size
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
        ValueError,
    ):
        return None

    output.seek(0)
    return ThumbnailResult(
        content=output,
        source_width=source_width,
        source_height=source_height,
        width=width,
        height=height,
    )
