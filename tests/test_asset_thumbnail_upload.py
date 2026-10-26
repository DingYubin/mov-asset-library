import shutil
import subprocess
from datetime import UTC, datetime
from io import BytesIO

import pytest
from app.db import Base, get_db
from app.models import Asset, SessionRecord
from app.routes import assets as asset_routes
from app.security import get_current_session, require_csrf
from app.storage import LocalStorage
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


def build_test_app(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    test_session = sessionmaker(bind=engine, expire_on_commit=False)
    storage = LocalStorage(tmp_path)
    monkeypatch.setattr(asset_routes, "get_storage", lambda: storage)

    session_record = SessionRecord(
        id="test-session",
        admin_user_id="test-admin",
        csrf_token="test-csrf",
        expires_at=datetime.now(UTC),
    )

    def override_db():
        with test_session() as db:
            yield db

    app = FastAPI()
    app.include_router(asset_routes.router)
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_session] = lambda: session_record
    app.dependency_overrides[require_csrf] = lambda: session_record
    return app, test_session, storage


def test_upload_image_creates_and_serves_thumbnail(tmp_path, monkeypatch):
    app, test_session, storage = build_test_app(tmp_path, monkeypatch)

    source = BytesIO()
    Image.new("RGB", (1600, 900), (24, 120, 100)).save(source, "PNG")

    with TestClient(app) as client:
        response = client.post(
            "/api/assets",
            data={"title": "缩略图测试", "category": "图片", "tags": "测试"},
            files={"file": ("preview.png", source.getvalue(), "image/png")},
        )

        assert response.status_code == 201, response.text
        payload = response.json()
        assert payload["cover_url"] == f"/api/assets/{payload['id']}/file/thumbnail"

        with test_session() as db:
            asset = db.scalar(select(Asset).where(Asset.id == payload["id"]))
            assert asset is not None
            files = {item.kind: item for item in asset.files}
            assert set(files) == {"original", "thumbnail"}
            assert asset.cover_key == files["thumbnail"].object_key
            assert files["original"].width == 1600
            assert files["original"].height == 900
            assert files["thumbnail"].mime_type == "image/webp"
            thumbnail = files["thumbnail"]

        assert (storage.root / thumbnail.object_key).is_file()

        preview = client.get(payload["cover_url"])
        assert preview.status_code == 200
        assert preview.headers["content-type"] == "image/webp"
        assert preview.headers["cache-control"] == "private, max-age=86400, immutable"
        with Image.open(BytesIO(preview.content)) as image:
            assert image.format == "WEBP"
            assert max(image.size) <= 720

        original = client.get(f"/api/assets/{payload['id']}/file/original")
        assert original.status_code == 200
        assert original.headers["cache-control"] == "private, max-age=86400, immutable"


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="ffmpeg and ffprobe are required for video thumbnail generation",
)
def test_upload_video_creates_first_frame_thumbnail(tmp_path, monkeypatch):
    app, test_session, storage = build_test_app(tmp_path, monkeypatch)
    video_path = tmp_path / "first-frame.mp4"
    subprocess.run(
        [
            shutil.which("ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x2f7d6d:s=1600x900:d=1:r=24",
            "-an",
            "-c:v",
            "mpeg4",
            "-q:v",
            "3",
            "-pix_fmt",
            "yuv420p",
            str(video_path),
        ],
        check=True,
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/assets",
            data={"title": "视频首帧测试", "category": "视频", "tags": "测试"},
            files={"file": ("first-frame.mp4", video_path.read_bytes(), "video/mp4")},
        )

        assert response.status_code == 201, response.text
        payload = response.json()
        assert payload["asset_type"] == "video"
        assert payload["cover_url"] == f"/api/assets/{payload['id']}/file/thumbnail"

        with test_session() as db:
            asset = db.scalar(select(Asset).where(Asset.id == payload["id"]))
            assert asset is not None
            files = {item.kind: item for item in asset.files}
            assert set(files) == {"original", "thumbnail"}
            assert files["original"].width == 1600
            assert files["original"].height == 900
            assert 900 <= (files["original"].duration_ms or 0) <= 1100
            thumbnail = files["thumbnail"]

        assert (storage.root / thumbnail.object_key).is_file()
        preview = client.get(payload["cover_url"])
        assert preview.status_code == 200
        assert preview.headers["content-type"] == "image/webp"
        with Image.open(BytesIO(preview.content)) as image:
            assert image.format == "WEBP"
            assert image.size == (720, 405)


def test_upload_video_succeeds_when_preview_generation_fails(tmp_path, monkeypatch):
    app, _, _ = build_test_app(tmp_path, monkeypatch)
    monkeypatch.setattr(asset_routes, "create_video_thumbnail", lambda *args: None)

    with TestClient(app) as client:
        response = client.post(
            "/api/assets",
            data={"title": "无预览视频", "category": "视频"},
            files={"file": ("broken.mp4", b"not a video", "video/mp4")},
        )

    assert response.status_code == 201, response.text
    assert response.json()["cover_url"] is None
