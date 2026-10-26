import hashlib
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import AdminUser, AuditLog, LoginThrottle, SessionRecord, now_utc
from ..schemas import LoginRequest, SessionResponse, UserResponse
from ..security import SESSION_COOKIE, as_utc, get_current_session, new_session, require_csrf, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


def audit(db: Session, action: str, request: Request, actor_id: str | None = None, detail: str = "") -> None:
    db.add(
        AuditLog(
            actor_id=actor_id,
            action=action,
            detail=detail,
            ip_address=request.client.host if request.client else "",
        )
    )


@router.get("/session", response_model=SessionResponse)
def session_status(request: Request, db: Session = Depends(get_db)) -> SessionResponse:
    try:
        session = get_current_session(request, db)
    except HTTPException:
        return SessionResponse(authenticated=False)
    user = db.get(AdminUser, session.admin_user_id)
    if not user or not user.is_active:
        return SessionResponse(authenticated=False)
    return SessionResponse(
        authenticated=True,
        csrf_token=session.csrf_token,
        user=UserResponse(id=user.id, username=user.username),
    )


@router.post("/login", response_model=SessionResponse)
def login(payload: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)) -> SessionResponse:
    client_ip = request.client.host if request.client else "unknown"
    throttle_id = hashlib.sha256(f"{client_ip}|{payload.username.casefold()}".encode()).hexdigest()
    throttle = db.get(LoginThrottle, throttle_id)
    now = now_utc()
    if throttle and throttle.blocked_until and as_utc(throttle.blocked_until) > now:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="登录尝试过多，请稍后再试")
    if throttle and (now - as_utc(throttle.window_started_at)).total_seconds() >= 15 * 60:
        throttle.failed_count = 0
        throttle.window_started_at = now
        throttle.blocked_until = None

    user = db.scalar(select(AdminUser).where(AdminUser.username == payload.username))
    if not user or not user.is_active or not verify_password(payload.password, user.password_hash):
        if not throttle:
            throttle = LoginThrottle(id=throttle_id, failed_count=0, window_started_at=now)
            db.add(throttle)
        throttle.failed_count += 1
        if throttle.failed_count >= 5:
            throttle.blocked_until = now + timedelta(minutes=15)
        audit(db, "login_failed", request, detail=f"username={payload.username[:80]}")
        db.commit()
        code = status.HTTP_429_TOO_MANY_REQUESTS if throttle.failed_count >= 5 else status.HTTP_401_UNAUTHORIZED
        detail = "登录尝试过多，请 15 分钟后再试" if code == 429 else "账号或密码错误"
        raise HTTPException(status_code=code, detail=detail)

    if throttle:
        db.delete(throttle)
    session = new_session(db, user, request)
    audit(db, "login_succeeded", request, user.id)
    db.commit()
    settings = get_settings()
    response.set_cookie(
        SESSION_COOKIE,
        session.id,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )
    return SessionResponse(
        authenticated=True,
        csrf_token=session.csrf_token,
        user=UserResponse(id=user.id, username=user.username),
    )


@router.post("/logout", status_code=204)
def logout(
    request: Request,
    response: Response,
    session: SessionRecord = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> None:
    session.revoked_at = now_utc()
    audit(db, "logout", request, session.admin_user_id)
    db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
