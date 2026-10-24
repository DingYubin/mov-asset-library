import hmac
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, status
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import AdminUser, SessionRecord, now_utc

pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")
SESSION_COOKIE = "mov_asset_session"
CSRF_HEADER = "X-CSRF-Token"
LOGIN_FAILURE_LIMIT = 5
LOGIN_WINDOW_SECONDS = 15 * 60


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def new_session(db: Session, user: AdminUser, request: Request) -> SessionRecord:
    settings = get_settings()
    now = now_utc()
    session = SessionRecord(
        id=secrets.token_urlsafe(48),
        admin_user_id=user.id,
        csrf_token=secrets.token_urlsafe(32),
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(seconds=settings.session_ttl_seconds),
        user_agent=request.headers.get("user-agent", "")[:512],
        ip_address=request.client.host if request.client else "",
    )
    db.add(session)
    db.flush()
    return session


def get_current_session(request: Request, db: Session = Depends(get_db)) -> SessionRecord:
    session_id = request.cookies.get(SESSION_COOKIE)
    if not session_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效")
    session = db.scalar(select(SessionRecord).where(SessionRecord.id == session_id))
    if not session or session.revoked_at or as_utc(session.expires_at) <= now_utc():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效")
    settings = get_settings()
    if (now_utc() - as_utc(session.last_seen_at)).total_seconds() > settings.session_idle_seconds:
        session.revoked_at = now_utc()
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录超时")
    session.last_seen_at = now_utc()
    db.commit()
    return session


def require_csrf(request: Request, session: SessionRecord = Depends(get_current_session)) -> SessionRecord:
    token = request.headers.get(CSRF_HEADER, "")
    if not token or not hmac.compare_digest(token, session.csrf_token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF 校验失败")
    return session
