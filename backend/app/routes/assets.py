import logging
import mimetypes
import secrets
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from ..config import get_settings
from ..db import get_db
from ..models import Asset, AssetFile, AuditLog, SessionRecord
from ..schemas import AssetResponse, AssetUpdate
from ..security import get_current_session, require_csrf
from ..storage import get_storage
from ..thumbnail_service import create_image_thumbnail

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/assets", tags=["assets"])


def tags_list(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def as_response(asset: Asset) -> AssetResponse:
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


def log(db: Session, session: SessionRecord, request: Request, action: str, asset_id: str | None = None) -> None:
    db.add(
        AuditLog(
            actor_id=session.admin_user_id,
            action=action,
            resource_type="asset",
            resource_id=asset_id,
            ip_address=request.client.host if request.client else "",
        )
    )


@router.get("", response_model=list[AssetResponse])
def list_assets(
    q: str = Query(default="", max_length=100),
    category: str = Query(default="", max_length=100),
    status_filter: str = Query(default="", alias="status", max_length=30),
    _: SessionRecord = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[AssetResponse]:
    stmt = select(Asset).options(selectinload(Asset.files)).order_by(Asset.updated_at.desc())
    if q:
        term = f"%{q}%"
        stmt = stmt.where(or_(Asset.title.ilike(term), Asset.description.ilike(term), Asset.tags.ilike(term)))
    if category:
        stmt = stmt.where(Asset.category == category)
    if status_filter:
        stmt = stmt.where(Asset.status == status_filter)
    return [as_response(asset) for asset in db.scalars(stmt).all()]


@router.post("", response_model=AssetResponse, status_code=201)
def create_asset(
    request: Request,
    title: str = Form(..., min_length=1, max_length=200),
    category: str = Form("未分类", max_length=100),
    tags: str = Form(""),
    description: str = Form(""),
    file: UploadFile = File(...),
    session: SessionRecord = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> AssetResponse:
    settings = get_settings()
    if not file.filename:
        raise HTTPException(status_code=400, detail="缺少文件名")
    file.file.seek(0, 2)
    declared_size = file.file.tell()
    file.file.seek(0)
    if declared_size > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="文件超过大小限制")
    original_name = Path(file.filename).name
    guessed_mime = mimetypes.guess_type(original_name)[0]
    mime_type = file.content_type or guessed_mime or "application/octet-stream"
    if mime_type == "application/octet-stream" and guessed_mime:
        mime_type = guessed_mime

    object_key = f"originals/{secrets.token_hex(16)}-{original_name}"
    storage = get_storage()
    size, digest = storage.save_upload(object_key, file.file, mime_type)
    asset_type = mime_type.split("/", 1)[0]
    asset = Asset(
        title=title.strip(),
        asset_type=asset_type,
        category=category.strip() or "未分类",
        tags=tags,
        description=description,
    )
    original = AssetFile(
        kind="original",
        object_key=object_key,
        original_name=original_name,
        mime_type=mime_type,
        size_bytes=size,
        sha256=digest,
    )
    asset.files.append(original)
    if asset_type == "image":
        try:
            thumbnail_result = create_image_thumbnail(storage, original, original_name)
            if thumbnail_result:
                thumbnail, source_width, source_height = thumbnail_result
                asset.files.append(thumbnail)
                asset.cover_key = thumbnail.object_key
                original.width = source_width
                original.height = source_height
        except Exception:
            # A preview failure must not stop the original asset from being stored.
            logger.exception("Failed to generate thumbnail for %s", original_name)
    db.add(asset)
    db.flush()
    log(db, session, request, "asset_created", asset.id)
    db.commit()
    db.refresh(asset)
    return as_response(asset)


@router.patch("/{asset_id}", response_model=AssetResponse)
def update_asset(
    asset_id: str,
    payload: AssetUpdate,
    request: Request,
    session: SessionRecord = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> AssetResponse:
    asset = db.scalar(select(Asset).options(selectinload(Asset.files)).where(Asset.id == asset_id))
    if not asset:
        raise HTTPException(status_code=404, detail="资产不存在")
    data = payload.model_dump(exclude_unset=True)
    if "tags" in data:
        data["tags"] = ",".join(data["tags"])
    for key, value in data.items():
        setattr(asset, key, value)
    log(db, session, request, "asset_updated", asset.id)
    db.commit()
    db.refresh(asset)
    return as_response(asset)


@router.delete("/{asset_id}", status_code=204)
def delete_asset(
    asset_id: str,
    request: Request,
    session: SessionRecord = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> None:
    asset = db.scalar(select(Asset).options(selectinload(Asset.files)).where(Asset.id == asset_id))
    if not asset:
        raise HTTPException(status_code=404, detail="资产不存在")
    storage = get_storage()
    for item in asset.files:
        storage.delete(item.object_key)
    log(db, session, request, "asset_deleted", asset.id)
    db.delete(asset)
    db.commit()


@router.get("/{asset_id}/file/{kind}")
def get_file(
    asset_id: str,
    kind: str,
    _: SessionRecord = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    asset = db.scalar(select(Asset).options(selectinload(Asset.files)).where(Asset.id == asset_id))
    if not asset:
        raise HTTPException(status_code=404, detail="资产不存在")
    item = next((file for file in asset.files if file.kind == kind), None)
    if not item:
        raise HTTPException(status_code=404, detail="文件不存在")
    stream, content_type = get_storage().open(item.object_key)
    safe_inline = content_type.startswith(("image/jpeg", "image/png", "image/gif", "image/webp", "video/", "audio/"))
    disposition = "inline" if safe_inline else "attachment"
    filename = quote(item.original_name, safe="")
    cache_control = "private, max-age=86400, immutable" if kind == "thumbnail" else "private, no-store"
    return StreamingResponse(
        stream,
        media_type=content_type,
        headers={
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{filename}",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": cache_control,
        },
    )
