from datetime import UTC, datetime
from io import BytesIO

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


def test_upload_image_creates_and_serves_thumbnail(tmp_path, monkeypatch):
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
        assert original.headers["cache-control"] == "private, no-store"
