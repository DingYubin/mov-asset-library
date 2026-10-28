from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..db import get_db
from ..models import (
    Asset,
    AuditLog,
    ScreenplayProject,
    ScreenplayScene,
    ScreenplaySceneAsset,
    SessionRecord,
    now_utc,
)
from ..schemas import (
    ScreenplayProjectCreate,
    ScreenplayProjectResponse,
    ScreenplayProjectSummary,
    ScreenplayProjectUpdate,
    ScreenplaySceneCreate,
    ScreenplaySceneOrder,
    ScreenplaySceneResponse,
    ScreenplaySceneUpdate,
)
from ..security import get_current_session, require_csrf
from ..serializers import asset_response

router = APIRouter(prefix="/api/screenplays", tags=["screenplays"])

PROJECT_LOAD_OPTIONS = (
    selectinload(ScreenplayProject.scenes)
    .selectinload(ScreenplayScene.asset_links)
    .selectinload(ScreenplaySceneAsset.asset)
    .selectinload(Asset.files)
)


def scene_response(scene: ScreenplayScene) -> ScreenplaySceneResponse:
    return ScreenplaySceneResponse(
        id=scene.id,
        position=scene.position,
        heading=scene.heading,
        content=scene.content,
        notes=scene.notes,
        assets=[asset_response(link.asset) for link in scene.asset_links if link.asset],
        created_at=scene.created_at,
        updated_at=scene.updated_at,
    )


def project_response(project: ScreenplayProject) -> ScreenplayProjectResponse:
    scenes = sorted(project.scenes, key=lambda item: item.position)
    return ScreenplayProjectResponse(
        id=project.id,
        title=project.title,
        logline=project.logline,
        status=project.status,
        scene_count=len(scenes),
        created_at=project.created_at,
        updated_at=project.updated_at,
        scenes=[scene_response(scene) for scene in scenes],
    )


def project_summary(project: ScreenplayProject) -> ScreenplayProjectSummary:
    return ScreenplayProjectSummary(
        id=project.id,
        title=project.title,
        logline=project.logline,
        status=project.status,
        scene_count=len(project.scenes),
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


def load_project(db: Session, project_id: str) -> ScreenplayProject:
    project = db.scalar(
        select(ScreenplayProject)
        .options(PROJECT_LOAD_OPTIONS)
        .where(ScreenplayProject.id == project_id)
    )
    if not project:
        raise HTTPException(status_code=404, detail="剧本项目不存在")
    return project


def find_scene(project: ScreenplayProject, scene_id: str) -> ScreenplayScene:
    scene = next((item for item in project.scenes if item.id == scene_id), None)
    if not scene:
        raise HTTPException(status_code=404, detail="场景不存在")
    return scene


def audit(
    db: Session,
    session: SessionRecord,
    request: Request,
    action: str,
    project_id: str,
) -> None:
    db.add(
        AuditLog(
            actor_id=session.admin_user_id,
            action=action,
            resource_type="screenplay_project",
            resource_id=project_id,
            ip_address=request.client.host if request.client else "",
        )
    )


@router.get("", response_model=list[ScreenplayProjectSummary])
def list_projects(
    _: SessionRecord = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[ScreenplayProjectSummary]:
    projects = db.scalars(
        select(ScreenplayProject)
        .options(selectinload(ScreenplayProject.scenes))
        .order_by(ScreenplayProject.updated_at.desc())
    ).all()
    return [project_summary(project) for project in projects]


@router.post("", response_model=ScreenplayProjectResponse, status_code=201)
def create_project(
    payload: ScreenplayProjectCreate,
    request: Request,
    session: SessionRecord = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> ScreenplayProjectResponse:
    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="项目名称不能为空")
    project = ScreenplayProject(
        title=title,
        logline=payload.logline.strip(),
        status=payload.status,
        scenes=[ScreenplayScene(position=0, heading="场景 1")],
    )
    db.add(project)
    db.flush()
    audit(db, session, request, "screenplay_created", project.id)
    db.commit()
    return project_response(project)


@router.get("/{project_id}", response_model=ScreenplayProjectResponse)
def get_project(
    project_id: str,
    _: SessionRecord = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> ScreenplayProjectResponse:
    return project_response(load_project(db, project_id))


@router.patch("/{project_id}", response_model=ScreenplayProjectResponse)
def update_project(
    project_id: str,
    payload: ScreenplayProjectUpdate,
    request: Request,
    session: SessionRecord = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> ScreenplayProjectResponse:
    project = load_project(db, project_id)
    data = payload.model_dump(exclude_unset=True)
    if "title" in data:
        data["title"] = data["title"].strip()
        if not data["title"]:
            raise HTTPException(status_code=422, detail="项目名称不能为空")
    if "logline" in data:
        data["logline"] = data["logline"].strip()
    for key, value in data.items():
        setattr(project, key, value)
    project.updated_at = now_utc()
    audit(db, session, request, "screenplay_updated", project.id)
    db.commit()
    return project_response(project)


@router.delete("/{project_id}", status_code=204)
def delete_project(
    project_id: str,
    request: Request,
    session: SessionRecord = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> None:
    project = load_project(db, project_id)
    audit(db, session, request, "screenplay_deleted", project.id)
    db.delete(project)
    db.commit()


@router.post(
    "/{project_id}/scenes",
    response_model=ScreenplaySceneResponse,
    status_code=201,
)
def create_scene(
    project_id: str,
    payload: ScreenplaySceneCreate,
    request: Request,
    session: SessionRecord = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> ScreenplaySceneResponse:
    project = load_project(db, project_id)
    position = max((scene.position for scene in project.scenes), default=-1) + 1
    scene = ScreenplayScene(
        project=project,
        position=position,
        heading=payload.heading.strip() or f"场景 {position + 1}",
        content=payload.content,
        notes=payload.notes,
    )
    db.add(scene)
    project.updated_at = now_utc()
    audit(db, session, request, "screenplay_scene_created", project.id)
    db.commit()
    return scene_response(scene)


@router.post("/{project_id}/scenes/reorder", response_model=ScreenplayProjectResponse)
def reorder_scenes(
    project_id: str,
    payload: ScreenplaySceneOrder,
    request: Request,
    session: SessionRecord = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> ScreenplayProjectResponse:
    project = load_project(db, project_id)
    current_ids = {scene.id for scene in project.scenes}
    if len(payload.scene_ids) != len(current_ids) or set(payload.scene_ids) != current_ids:
        raise HTTPException(status_code=422, detail="场景排序数据不完整")
    by_id = {scene.id: scene for scene in project.scenes}
    for position, scene_id in enumerate(payload.scene_ids):
        by_id[scene_id].position = position
    project.updated_at = now_utc()
    audit(db, session, request, "screenplay_scenes_reordered", project.id)
    db.commit()
    return project_response(project)


@router.patch(
    "/{project_id}/scenes/{scene_id}",
    response_model=ScreenplaySceneResponse,
)
def update_scene(
    project_id: str,
    scene_id: str,
    payload: ScreenplaySceneUpdate,
    request: Request,
    session: SessionRecord = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> ScreenplaySceneResponse:
    project = load_project(db, project_id)
    scene = find_scene(project, scene_id)
    data = payload.model_dump(exclude_unset=True)
    if "heading" in data:
        data["heading"] = data["heading"].strip()
    for key, value in data.items():
        setattr(scene, key, value)
    scene.updated_at = now_utc()
    project.updated_at = now_utc()
    db.commit()
    return scene_response(scene)


@router.delete("/{project_id}/scenes/{scene_id}", status_code=204)
def delete_scene(
    project_id: str,
    scene_id: str,
    request: Request,
    session: SessionRecord = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> None:
    project = load_project(db, project_id)
    if len(project.scenes) <= 1:
        raise HTTPException(status_code=400, detail="剧本至少需要保留一个场景")
    scene = find_scene(project, scene_id)
    remaining = [item for item in project.scenes if item.id != scene_id]
    for position, item in enumerate(remaining):
        item.position = position
    project.updated_at = now_utc()
    audit(db, session, request, "screenplay_scene_deleted", project.id)
    db.delete(scene)
    db.commit()


@router.post(
    "/{project_id}/scenes/{scene_id}/assets/{asset_id}",
    response_model=ScreenplaySceneResponse,
)
def link_asset(
    project_id: str,
    scene_id: str,
    asset_id: str,
    request: Request,
    session: SessionRecord = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> ScreenplaySceneResponse:
    project = load_project(db, project_id)
    scene = find_scene(project, scene_id)
    asset = db.scalar(select(Asset).options(selectinload(Asset.files)).where(Asset.id == asset_id))
    if not asset:
        raise HTTPException(status_code=404, detail="资产不存在")
    if not any(link.asset_id == asset_id for link in scene.asset_links):
        scene.asset_links.append(ScreenplaySceneAsset(asset=asset))
        project.updated_at = now_utc()
        audit(db, session, request, "screenplay_asset_linked", project.id)
        db.commit()
    return scene_response(scene)


@router.delete(
    "/{project_id}/scenes/{scene_id}/assets/{asset_id}",
    status_code=204,
)
def unlink_asset(
    project_id: str,
    scene_id: str,
    asset_id: str,
    request: Request,
    session: SessionRecord = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> None:
    project = load_project(db, project_id)
    scene = find_scene(project, scene_id)
    link = next((item for item in scene.asset_links if item.asset_id == asset_id), None)
    if link:
        scene.asset_links.remove(link)
        project.updated_at = now_utc()
        audit(db, session, request, "screenplay_asset_unlinked", project.id)
        db.commit()
