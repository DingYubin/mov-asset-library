from io import BytesIO

from app.thumbnails import THUMBNAIL_MAX_EDGE, generate_image_thumbnail
from PIL import Image


def test_generate_image_thumbnail_preserves_ratio_and_metadata():
    source = BytesIO()
    Image.new("RGBA", (1600, 900), (20, 120, 100, 180)).save(source, "PNG")
    source.seek(0)

    result = generate_image_thumbnail(source)

    assert result is not None
    assert result.source_width == 1600
    assert result.source_height == 900
    assert max(result.width, result.height) <= THUMBNAIL_MAX_EDGE
    result.content.seek(0)
    with Image.open(result.content) as thumbnail:
        assert thumbnail.format == "WEBP"
        assert thumbnail.size == (result.width, result.height)


def test_generate_image_thumbnail_rejects_non_image_content():
    result = generate_image_thumbnail(BytesIO(b"not an image"))

    assert result is None
