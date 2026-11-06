import hashlib
import hmac
import mimetypes
import secrets
import tempfile
from pathlib import Path
from typing import Annotated, BinaryIO
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request as UrlRequest
from urllib.request import urlopen

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..config import get_settings
from ..db import get_db
from ..models import (
    Asset,
    AssetFile,
    AuditLog,
    ScreenplayProject,
    ScreenplayScene,
    ScreenplaySceneAsset,
)
from ..storage import Storage, get_storage

router = APIRouter(prefix="/api/integration/assets", tags=["integration-assets"])
public_router = APIRouter(prefix="/api/public/assets", tags=["public-assets"])

CHUNK_SIZE = 1024 * 1024
DbSession = Annotated[Session, Depends(get_db)]


class IntegrationAssetResponse(BaseModel):
    asset_id: str
    title: str
    file_status: str
    asset_status: str
    visibility: str
    original_name: str
    mime_type: str
    size_bytes: int
    sha256: str
    public_url: str | None = None
    scene_id: str | None = None
    verified: bool = False


class PublishAssetResponse(BaseModel):
    asset_id: str
    asset_status: str
    visibility: str
    public_url: str
    mime_type: str
    size_bytes: int
    sha256: str
    verified: bool


def require_integration_token(request: Request) -> None:
    settings = get_settings()
    expected = settings.asset_library_api_token.strip()
    if not expected:
        raise HTTPException(status_code=503, detail="资产库集成接口未启用")

    scheme, separator, supplied = request.headers.get("authorization", "").partition(" ")
    if (
        separator != " "
        or scheme.lower() != "bearer"
        or not supplied
        or not hmac.compare_digest(supplied, expected)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="集成令牌无效",
            headers={"WWW-Authenticate": "Bearer"},
        )


IntegrationToken = Annotated[None, Depends(require_integration_token)]


def _original_file(db: Session, asset_id: str) -> tuple[Asset, AssetFile] | None:
    asset = db.scalar(
        select(Asset).options(selectinload(Asset.files)).where(Asset.id == asset_id)
    )
    if not asset:
        return None
    original = next((item for item in asset.files if item.kind == "original"), None)
    if not original:
        return None
    return asset, original


def _public_url(base_url: str, asset_id: str) -> str:
    base = base_url.strip().rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("资产库公网基址无效")
    return f"{base}/api/public/assets/{quote(asset_id, safe='')}/file"


def _response_for(
    asset: Asset,
    original: AssetFile,
    file_status: str,
    scene_id: str | None,
    base_url: str,
) -> IntegrationAssetResponse:
    public_url = None
    if asset.status == "published" and asset.visibility == "public" and base_url:
        public_url = _public_url(base_url, asset.id)
    return IntegrationAssetResponse(
        asset_id=asset.id,
        title=asset.title,
        file_status=file_status,
        asset_status=asset.status,
        visibility=asset.visibility,
        original_name=original.original_name,
        mime_type=original.mime_type,
        size_bytes=original.size_bytes,
        sha256=original.sha256,
        public_url=public_url,
        scene_id=scene_id,
        verified=public_url is not None,
    )


def _validate_scene(
    db: Session,
    project_id: str | None,
    scene_id: str | None,
) -> ScreenplayScene | None:
    if not project_id and not scene_id:
        return None
    if not project_id or not scene_id:
        raise HTTPException(status_code=400, detail="project_id 和 scene_id 必须同时提供")

    project = db.get(ScreenplayProject, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="剧本项目不存在")
    scene = db.get(ScreenplayScene, scene_id)
    if not scene:
        raise HTTPException(status_code=404, detail="分镜不存在")
    if scene.project_id != project_id:
        raise HTTPException(status_code=400, detail="分镜不属于指定剧本项目")
    return scene


def _link_scene(db: Session, scene: ScreenplayScene | None, asset: Asset) -> None:
    if not scene:
        return
    existing = db.get(ScreenplaySceneAsset, (scene.id, asset.id))
    if not existing:
        db.add(ScreenplaySceneAsset(scene_id=scene.id, asset_id=asset.id))


def _stage_upload(file: UploadFile, max_bytes: int) -> tuple[BinaryIO, int, str]:
    staged = tempfile.TemporaryFile("w+b")  # noqa: SIM115 - caller closes it in a finally block
    digest = hashlib.sha256()
    size = 0
    try:
        while chunk := file.file.read(CHUNK_SIZE):
            size += len(chunk)
            if size > max_bytes:
                raise HTTPException(status_code=413, detail="文件超过大小限制")
            staged.write(chunk)
            digest.update(chunk)
        if size == 0:
            raise HTTPException(status_code=400, detail="上传文件为空")
        staged.seek(0)
        return staged, size, digest.hexdigest()
    except Exception:
        staged.close()
        raise


def _verify_public_file(url: str, expected: AssetFile, timeout_seconds: int) -> None:
    request = UrlRequest(
        url,
        headers={"User-Agent": "mov-asset-library-publish-verifier/1.0"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            response_status = getattr(response, "status", response.getcode())
            if response_status != 200:
                raise ValueError(f"公网返回 HTTP {response_status}")

            content_type = (response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            expected_type = expected.mime_type.split(";", 1)[0].strip().lower()
            if content_type != expected_type:
                raise ValueError("公网文件 MIME 类型不一致")

            content_length = response.headers.get("Content-Length")
            if content_length is None:
                raise ValueError("公网响应缺少 Content-Length")
            try:
                declared_size = int(content_length)
            except ValueError as exc:
                raise ValueError("公网 Content-Length 无效") from exc
            if declared_size != expected.size_bytes:
                raise ValueError("公网文件长度不一致")

            digest = hashlib.sha256()
            received = 0
            while chunk := response.read(CHUNK_SIZE):
                received += len(chunk)
                digest.update(chunk)
            if received != expected.size_bytes:
                raise ValueError("公网文件实际长度不一致")
            if digest.hexdigest() != expected.sha256:
                raise ValueError("公网文件 SHA-256 不一致")
    except HTTPError as exc:
        raise ValueError(f"公网文件返回 HTTP {exc.code}") from exc
    except URLError as exc:
        raise ValueError(f"无法读取公网文件：{exc.reason}") from exc


@router.get("/{asset_id}", response_model=IntegrationAssetResponse)
def get_integration_asset(
    asset_id: str,
    _: IntegrationToken,
    db: DbSession,
) -> IntegrationAssetResponse:
    result = _original_file(db, asset_id)
    if not result:
        raise HTTPException(status_code=404, detail="资产或原文件不存在")
    asset, original = result
    scene_id = db.scalar(
        select(ScreenplaySceneAsset.scene_id)
        .where(ScreenplaySceneAsset.asset_id == asset.id)
        .order_by(ScreenplaySceneAsset.created_at)
        .limit(1)
    )
    return _response_for(
        asset,
        original,
        "existing",
        scene_id,
        get_settings().asset_library_public_base_url,
    )


@router.post("", response_model=IntegrationAssetResponse, status_code=201)
def create_or_reuse_asset(
    response: Response,
    title: Annotated[str, Form(min_length=1, max_length=200)],
    file: Annotated[UploadFile, File()],
    _: IntegrationToken,
    db: DbSession,
    category: Annotated[str, Form(max_length=100)] = "未分类",
    tags: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
    project_id: Annotated[str | None, Form()] = None,
    scene_id: Annotated[str | None, Form()] = None,
) -> IntegrationAssetResponse:
    settings = get_settings()
    scene = _validate_scene(db, project_id, scene_id)
    if not file.filename:
        raise HTTPException(status_code=400, detail="缺少文件名")

    original_name = Path(file.filename).name.strip()
    if not original_name:
        raise HTTPException(status_code=400, detail="文件名无效")
    guessed_mime = mimetypes.guess_type(original_name)[0]
    mime_type = file.content_type or guessed_mime or "application/octet-stream"
    if mime_type == "application/octet-stream" and guessed_mime:
        mime_type = guessed_mime

    staged, size, digest = _stage_upload(file, settings.max_upload_bytes)
    try:
        reused_asset = db.scalar(
            select(Asset)
            .join(AssetFile)
            .options(selectinload(Asset.files))
            .where(
                AssetFile.kind == "original",
                AssetFile.sha256 == digest,
            )
            .order_by(Asset.created_at)
            .limit(1)
        )
        if reused_asset:
            existing_original = next(
                (item for item in reused_asset.files if item.kind == "original"),
                None,
            )
            if not existing_original:
                raise HTTPException(status_code=500, detail="复用资产缺少原文件记录")
            _link_scene(db, scene, reused_asset)
            db.add(
                AuditLog(
                    action="integration_asset_reused",
                    resource_type="asset",
                    resource_id=reused_asset.id,
                    detail=f"scene_id={scene.id}" if scene else "",
                )
            )
            db.commit()
            response.status_code = status.HTTP_200_OK
            return _response_for(
                reused_asset,
                existing_original,
                "reused",
                scene.id if scene else None,
                settings.asset_library_public_base_url,
            )

        storage: Storage = get_storage()
        object_key = f"originals/{secrets.token_hex(16)}-{original_name}"
        stored_size, stored_digest = storage.save_upload(object_key, staged, mime_type)
        if stored_size != size or stored_digest != digest:
            storage.delete(object_key)
            raise HTTPException(status_code=500, detail="文件写入校验失败")

        asset = Asset(
            title=title.strip(),
            asset_type=mime_type.split("/", 1)[0],
            category=category.strip() or "未分类",
            tags=tags,
            description=description,
            status="draft",
            visibility="private",
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
        try:
            db.add(asset)
            db.flush()
            _link_scene(db, scene, asset)
            db.add(
                AuditLog(
                    action="integration_asset_created",
                    resource_type="asset",
                    resource_id=asset.id,
                    detail=f"scene_id={scene.id}" if scene else "",
                )
            )
            db.commit()
        except Exception:
            db.rollback()
            storage.delete(object_key)
            raise
        db.refresh(asset)
        return _response_for(
            asset,
            original,
            "created",
            scene.id if scene else None,
            settings.asset_library_public_base_url,
        )
    finally:
        staged.close()


@router.post("/{asset_id}/publish", response_model=PublishAssetResponse)
def publish_asset(
    asset_id: str,
    _: IntegrationToken,
    db: DbSession,
) -> PublishAssetResponse:
    result = _original_file(db, asset_id)
    if not result:
        raise HTTPException(status_code=404, detail="资产或原文件不存在")
    asset, original = result
    settings = get_settings()
    try:
        public_url = _public_url(settings.asset_library_public_base_url, asset.id)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    previous_status = asset.status
    previous_visibility = asset.visibility
    asset.status = "published"
    asset.visibility = "public"
    db.commit()

    try:
        _verify_public_file(
            public_url,
            original,
            settings.asset_library_publish_verify_timeout_seconds,
        )
    except Exception as exc:
        asset.status = previous_status
        asset.visibility = previous_visibility
        db.commit()
        raise HTTPException(
            status_code=502,
            detail=f"公网回读校验失败，发布已回滚：{exc}",
        ) from exc

    db.add(
        AuditLog(
            action="integration_asset_published",
            resource_type="asset",
            resource_id=asset.id,
            detail=public_url,
        )
    )
    db.commit()
    return PublishAssetResponse(
        asset_id=asset.id,
        asset_status=asset.status,
        visibility=asset.visibility,
        public_url=public_url,
        mime_type=original.mime_type,
        size_bytes=original.size_bytes,
        sha256=original.sha256,
        verified=True,
    )


@public_router.get("/{asset_id}/file")
def get_public_file(
    asset_id: str,
    db: DbSession,
) -> StreamingResponse:
    result = _original_file(db, asset_id)
    if not result:
        raise HTTPException(status_code=404, detail="文件不存在")
    asset, original = result
    if asset.status != "published" or asset.visibility != "public":
        raise HTTPException(status_code=404, detail="文件不存在")

    stream, _ = get_storage().open(original.object_key)
    safe_inline = original.mime_type.startswith(
        ("image/jpeg", "image/png", "image/gif", "image/webp", "video/", "audio/")
    )
    disposition = "inline" if safe_inline else "attachment"
    filename = quote(original.original_name, safe="")
    return StreamingResponse(
        stream,
        media_type=original.mime_type,
        headers={
            "Content-Length": str(original.size_bytes),
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{filename}",
            "ETag": f'"{original.sha256}"',
            "Cache-Control": "public, max-age=300",
            "X-Content-Type-Options": "nosniff",
        },
    )
