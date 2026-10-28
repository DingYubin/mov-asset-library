from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from urllib.parse import urlsplit

from .config import get_settings
from .db import init_db
from .routes import assets, auth, screenplays

settings = get_settings()
app = FastAPI(title="MOV Asset Library API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-CSRF-Token"],
)


@app.middleware("http")
async def enforce_browser_origin(request: Request, call_next):
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        origin = request.headers.get("origin")
        if origin:
            origin_url = urlsplit(origin)
            origin_host = origin_url.netloc
            request_host = request.headers.get("host", "")
            local_dev_origin = settings.app_env == "development" and origin_url.hostname in {"127.0.0.1", "localhost"}
            if origin_host != request_host and not local_dev_origin:
                return JSONResponse(status_code=403, content={"detail": "跨站请求已拒绝"})
    content_length = request.headers.get("content-length")
    if content_length and request.url.path.startswith("/api/"):
        if int(content_length) > settings.max_upload_bytes + 1024 * 1024:
            return JSONResponse(status_code=413, content={"detail": "请求体超过大小限制"})
    return await call_next(request)
app.include_router(auth.router)
app.include_router(assets.router)
app.include_router(screenplays.router)


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "mov-asset-library", "environment": settings.app_env}
