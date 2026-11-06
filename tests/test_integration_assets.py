import hashlib
from io import BytesIO
from types import SimpleNamespace

import pytest
from app.db import Base, get_db
from app.models import (
    Asset,
    ScreenplayProject,
    ScreenplayScene,
    ScreenplaySceneAsset,
)
from app.routes import integration
from app.storage import LocalStorage
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

TOKEN = "test-integration-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def build_test_app(tmp_path, monkeypatch, *, configured=True):
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    test_session = sessionmaker(bind=engine, expire_on_commit=False)
    storage = LocalStorage(tmp_path / "storage")
    settings = SimpleNamespace(
        asset_library_api_token=TOKEN if configured else "",
        asset_library_public_base_url="https://assets.example.test",
        asset_library_publish_verify_timeout_seconds=1,
        max_upload_bytes=1024 * 1024,
    )
    monkeypatch.setattr(integration, "get_settings", lambda: settings)
    monkeypatch.setattr(integration, "get_storage", lambda: storage)

    def override_db():
        with test_session() as db:
            yield db

    app = FastAPI()
    app.include_router(integration.router)
    app.include_router(integration.public_router)
    app.dependency_overrides[get_db] = override_db
    return app, test_session, storage


def upload_asset(client, content=b"reference-frame", **data):
    return client.post(
        "/api/integration/assets",
        headers=AUTH,
        data={"title": "主角参考帧", **data},
        files={"file": ("frame.png", content, "image/png")},
    )


def test_integration_token_disabled_missing_and_wrong(tmp_path, monkeypatch):
    disabled_app, _, _ = build_test_app(tmp_path, monkeypatch, configured=False)
    with TestClient(disabled_app) as client:
        response = client.get("/api/integration/assets/not-found")
    assert response.status_code == 503

    app, _, _ = build_test_app(tmp_path / "enabled", monkeypatch)
    with TestClient(app) as client:
        missing = client.get("/api/integration/assets/not-found")
        wrong = client.get(
            "/api/integration/assets/not-found",
            headers={"Authorization": "Bearer wrong"},
        )
        valid = client.get("/api/integration/assets/not-found", headers=AUTH)

    assert missing.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"
    assert wrong.status_code == 401
    assert valid.status_code == 404


def test_upload_is_private_and_duplicate_upload_is_reused(tmp_path, monkeypatch):
    app, _, _ = build_test_app(tmp_path, monkeypatch)
    content = b"same reference frame"
    expected_hash = hashlib.sha256(content).hexdigest()

    with TestClient(app) as client:
        created = upload_asset(client, content)
        assert created.status_code == 201, created.text
        payload = created.json()
        assert payload["file_status"] == "created"
        assert payload["asset_status"] == "draft"
        assert payload["visibility"] == "private"
        assert payload["verified"] is False
        assert payload["public_url"] is None
        assert payload["sha256"] == expected_hash
        assert payload["size_bytes"] == len(content)

        private_file = client.get(f"/api/public/assets/{payload['asset_id']}/file")
        assert private_file.status_code == 404

        reused = upload_asset(client, content, title="重复上传")
        assert reused.status_code == 200, reused.text
        reused_payload = reused.json()
        assert reused_payload["asset_id"] == payload["asset_id"]
        assert reused_payload["file_status"] == "reused"


def test_publish_returns_verified_public_url(tmp_path, monkeypatch):
    app, _, storage = build_test_app(tmp_path, monkeypatch)
    verified = []

    def verify(url, original, timeout_seconds):
        verified.append((url, original.sha256, timeout_seconds))

    monkeypatch.setattr(integration, "_verify_public_file", verify)
    content = b"publishable reference frame"

    with TestClient(app) as client:
        created = upload_asset(client, content)
        asset_id = created.json()["asset_id"]
        published = client.post(
            f"/api/integration/assets/{asset_id}/publish",
            headers=AUTH,
        )
        assert published.status_code == 200, published.text
        payload = published.json()
        expected_url = (
            f"https://assets.example.test/api/public/assets/{asset_id}/file"
        )
        assert payload == {
            "asset_id": asset_id,
            "asset_status": "published",
            "visibility": "public",
            "public_url": expected_url,
            "mime_type": "image/png",
            "size_bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
            "verified": True,
        }
        assert verified == [
            (expected_url, hashlib.sha256(content).hexdigest(), 1)
        ]

        public_file = client.get(f"/api/public/assets/{asset_id}/file")
        assert public_file.status_code == 200
        assert public_file.content == content
        assert public_file.headers["content-type"] == "image/png"
        assert public_file.headers["content-length"] == str(len(content))
        assert public_file.headers["etag"] == f'"{hashlib.sha256(content).hexdigest()}"'

    stored = list(storage.root.rglob("*"))
    assert any(path.is_file() for path in stored)


def test_publish_verification_failure_rolls_back_visibility(tmp_path, monkeypatch):
    app, test_session, _ = build_test_app(tmp_path, monkeypatch)
    monkeypatch.setattr(
        integration,
        "_verify_public_file",
        lambda *args: (_ for _ in ()).throw(ValueError("公网文件 SHA-256 不一致")),
    )

    with TestClient(app) as client:
        created = upload_asset(client, b"rollback reference frame")
        asset_id = created.json()["asset_id"]
        response = client.post(
            f"/api/integration/assets/{asset_id}/publish",
            headers=AUTH,
        )
        assert response.status_code == 502
        assert "发布已回滚" in response.json()["detail"]

        private_file = client.get(f"/api/public/assets/{asset_id}/file")
        assert private_file.status_code == 404

    with test_session() as db:
        asset = db.scalar(select(Asset).where(Asset.id == asset_id))
        assert asset is not None
        assert asset.status == "draft"
        assert asset.visibility == "private"


def test_scene_must_belong_to_project_and_valid_link_is_recorded(tmp_path, monkeypatch):
    app, test_session, _ = build_test_app(tmp_path, monkeypatch)
    with test_session() as db:
        project = ScreenplayProject(title="天缝")
        other_project = ScreenplayProject(title="另一项目")
        db.add_all([project, other_project])
        db.flush()
        scene = ScreenplayScene(project_id=project.id, heading="第一场")
        other_scene = ScreenplayScene(project_id=other_project.id, heading="第二场")
        db.add_all([scene, other_scene])
        db.commit()
        project_id, scene_id = project.id, scene.id
        other_scene_id = other_scene.id

    with TestClient(app) as client:
        rejected = client.post(
            "/api/integration/assets",
            headers=AUTH,
            data={
                "title": "错误归属",
                "project_id": project_id,
                "scene_id": other_scene_id,
            },
            files={"file": ("wrong.png", b"wrong scene", "image/png")},
        )
        assert rejected.status_code == 400

        accepted = client.post(
            "/api/integration/assets",
            headers=AUTH,
            data={"title": "正确归属", "project_id": project_id, "scene_id": scene_id},
            files={"file": ("right.png", b"right scene", "image/png")},
        )
        assert accepted.status_code == 201, accepted.text
        asset_id = accepted.json()["asset_id"]

    with test_session() as db:
        link = db.get(ScreenplaySceneAsset, (scene_id, asset_id))
        assert link is not None


def test_local_storage_rejects_path_traversal(tmp_path):
    storage = LocalStorage(tmp_path / "storage")

    with pytest.raises(ValueError, match="Invalid storage key"):
        storage.save_upload("../escape.txt", BytesIO(b"escape"))

    assert not (tmp_path / "escape.txt").exists()
