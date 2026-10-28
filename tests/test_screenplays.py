from datetime import UTC, datetime, timedelta

from app.db import Base, get_db
from app.models import Asset, AssetFile, ScreenplayProject, ScreenplaySceneAsset, SessionRecord
from app.routes import screenplays
from app.security import get_current_session, require_csrf
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


def build_test_app():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    test_session = sessionmaker(bind=engine, expire_on_commit=False)
    auth_session = SessionRecord(
        id="test-session",
        admin_user_id="test-admin",
        csrf_token="test-csrf",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    def override_db():
        with test_session() as db:
            yield db

    app = FastAPI()
    app.include_router(screenplays.router)
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_session] = lambda: auth_session
    app.dependency_overrides[require_csrf] = lambda: auth_session
    return app, test_session


def test_create_and_edit_screenplay_persists(tmp_path):
    app, _ = build_test_app()

    with TestClient(app) as client:
        created = client.post(
            "/api/screenplays",
            json={"title": " 夜航  ", "logline": "一艘船驶向没有地图的海域。"},
        )
        assert created.status_code == 201, created.text
        project = created.json()
        assert project["title"] == "夜航"
        assert project["scene_count"] == 1
        assert project["scenes"][0]["heading"] == "场景 1"

        scene_id = project["scenes"][0]["id"]
        updated = client.patch(
            f"/api/screenplays/{project['id']}/scenes/{scene_id}",
            json={
                "heading": "EXT. 海面 - 夜",
                "content": "探照灯扫过黑色海面。\\n远处传来发动机的低鸣。",
                "notes": "保留长焦和潮水声。",
            },
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["heading"] == "EXT. 海面 - 夜"

        loaded = client.get(f"/api/screenplays/{project['id']}")
        assert loaded.status_code == 200
        payload = loaded.json()
        assert payload["scenes"][0]["content"].startswith("探照灯")
        assert payload["scenes"][0]["notes"] == "保留长焦和潮水声。"


def test_scenes_can_be_added_reordered_and_deleted():
    app, _ = build_test_app()

    with TestClient(app) as client:
        project = client.post("/api/screenplays", json={"title": "分镜测试"}).json()
        project_id = project["id"]
        first_id = project["scenes"][0]["id"]

        second = client.post(f"/api/screenplays/{project_id}/scenes", json={})
        third = client.post(
            f"/api/screenplays/{project_id}/scenes",
            json={"heading": "INT. 地下室 - 深夜"},
        )
        assert second.status_code == 201, second.text
        assert third.status_code == 201, third.text
        second_id = second.json()["id"]
        third_id = third.json()["id"]

        reordered = client.post(
            f"/api/screenplays/{project_id}/scenes/reorder",
            json={"scene_ids": [third_id, first_id, second_id]},
        )
        assert reordered.status_code == 200, reordered.text
        assert [scene["id"] for scene in reordered.json()["scenes"]] == [
            third_id,
            first_id,
            second_id,
        ]
        assert [scene["position"] for scene in reordered.json()["scenes"]] == [0, 1, 2]

        deleted = client.delete(f"/api/screenplays/{project_id}/scenes/{first_id}")
        assert deleted.status_code == 204, deleted.text
        loaded = client.get(f"/api/screenplays/{project_id}").json()
        assert [scene["id"] for scene in loaded["scenes"]] == [third_id, second_id]
        assert loaded["scenes"][0]["position"] == 0
        assert loaded["scenes"][1]["position"] == 1


def test_scene_assets_can_be_linked_and_removed():
    app, test_session = build_test_app()

    with test_session() as db:
        asset = Asset(
            id="asset-1",
            title="海面参考",
            asset_type="image",
            description="",
            category="图片",
            tags="海面,夜景",
            files=[
                AssetFile(
                    id="file-1",
                    kind="original",
                    object_key="originals/sea.png",
                    original_name="sea.png",
                    mime_type="image/png",
                    size_bytes=1024,
                )
            ],
        )
        db.add(asset)
        db.commit()

    with TestClient(app) as client:
        project = client.post("/api/screenplays", json={"title": "素材测试"}).json()
        scene_id = project["scenes"][0]["id"]
        link_url = f"/api/screenplays/{project['id']}/scenes/{scene_id}/assets/asset-1"

        linked = client.post(link_url)
        assert linked.status_code == 200, linked.text
        assert [asset["id"] for asset in linked.json()["assets"]] == ["asset-1"]
        assert linked.json()["assets"][0]["cover_url"] == "/api/assets/asset-1/file/original"

        duplicate = client.post(link_url)
        assert duplicate.status_code == 200
        assert len(duplicate.json()["assets"]) == 1

        unlinked = client.delete(link_url)
        assert unlinked.status_code == 204, unlinked.text
        loaded = client.get(f"/api/screenplays/{project['id']}").json()
        assert loaded["scenes"][0]["assets"] == []

    with test_session() as db:
        assert db.scalar(select(ScreenplaySceneAsset)) is None
        assert db.scalar(select(ScreenplayProject)) is not None
