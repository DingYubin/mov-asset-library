from .models import Asset
from .schemas import AssetResponse


def tags_list(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def asset_response(asset: Asset) -> AssetResponse:
    original = next((item for item in asset.files if item.kind == "original"), None)
    cover = next((item for item in asset.files if item.kind in {"cover", "thumbnail"}), None)
    cover_url = f"/api/assets/{asset.id}/file/{cover.kind}" if cover else None
    if not cover_url and original and asset.asset_type == "image":
        # Older image assets may not have a generated thumbnail yet.
        cover_url = f"/api/assets/{asset.id}/file/original"
    return AssetResponse(
        id=asset.id,
        title=asset.title,
        asset_type=asset.asset_type,
        description=asset.description,
        category=asset.category,
        tags=tags_list(asset.tags),
        status=asset.status,
        visibility=asset.visibility,
        cover_url=cover_url,
        original_name=original.original_name if original else None,
        mime_type=original.mime_type if original else None,
        size_bytes=original.size_bytes if original else 0,
        created_at=asset.created_at,
        updated_at=asset.updated_at,
    )
