#!/usr/bin/env python3
"""Run one DMC video task through download validation and asset publication."""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import secrets
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path
from typing import Any, BinaryIO
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

DMC_DEFAULT_BASE_URL = "https://api.ll2.cn/seedance"
ASSET_LIBRARY_DEFAULT_BASE_URL = "https://119.91.33.40"
SUBMIT_PATH = "/api/v3/contents/generations/tasks"
DOWNLOAD_CHUNK_SIZE = 1024 * 1024
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_REQUEST_FILE_BYTES = 2 * 1024 * 1024
DEFAULT_POLL_TIMEOUT_SECONDS = 15 * 60
DEFAULT_MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024 * 1024


class WorkflowError(RuntimeError):
    """A safe, user-facing workflow error."""

    def __init__(self, code: str, message: str, *, provider_code: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.provider_code = provider_code


@dataclass(frozen=True)
class FetchMetadata:
    mime_type: str
    content_length: int | None
    size_bytes: int
    sha256: str


def _join_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _absolute_http_url(value: str, label: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise WorkflowError("invalid_url", f"{label}不是有效的 HTTP(S) URL")
    if parsed.username or parsed.password:
        raise WorkflowError("invalid_url", f"{label}不得包含凭据")
    return value


def resolve_dmc_url(base_url: str, value: str) -> str:
    """Resolve a provider-relative result path without dropping the DMC prefix."""

    parsed = urlsplit(value)
    if parsed.scheme:
        return _absolute_http_url(value, "DMC 视频地址")
    return _absolute_http_url(_join_url(base_url, value), "DMC 视频地址")


def _same_origin(left: str, right: str) -> bool:
    left_url, right_url = urlsplit(left), urlsplit(right)
    default_port = {"http": 80, "https": 443}
    return (
        left_url.scheme == right_url.scheme
        and left_url.hostname == right_url.hostname
        and (left_url.port or default_port[left_url.scheme])
        == (right_url.port or default_port[right_url.scheme])
    )


def _redact(value: str, secret_values: Sequence[str]) -> str:
    redacted = value
    for secret in secret_values:
        if secret:
            redacted = redacted.replace(secret, "<redacted>")
    return redacted


def _response_request_id(headers: Mapping[str, str]) -> str | None:
    return headers.get("x-oneapi-request-id") or headers.get("x-request-id")


def _error_detail(exc: HTTPError, secret_values: Sequence[str]) -> str:
    detail = f"HTTP {exc.code}"
    request_id = _response_request_id(exc.headers)
    if request_id:
        detail += f" (request_id={request_id})"
    try:
        raw = exc.read(4096).decode("utf-8", errors="replace").strip()
    except OSError:
        raw = ""
    if raw:
        try:
            payload = json.loads(raw)
            candidate = payload.get("detail") or payload.get("message")
            if not candidate and isinstance(payload.get("error"), dict):
                candidate = payload["error"].get("message")
            if isinstance(candidate, str) and candidate.strip():
                raw = candidate.strip()
        except json.JSONDecodeError:
            pass
        detail += f": {_redact(raw[:500], secret_values)}"
    return detail


class _MultipartBody:
    """A seekable-length, file-backed multipart body for urllib streaming."""

    def __init__(
        self,
        *,
        fields: Mapping[str, str],
        file_path: Path,
        field_name: str,
        file_name: str,
        file_mime_type: str,
    ) -> None:
        boundary = f"mov-asset-{secrets.token_hex(16)}"
        self.content_type = f"multipart/form-data; boundary={boundary}"
        self._segments: list[tuple[bytes | Path, int]] = []
        for name, value in fields.items():
            safe_name = name.replace('"', "%22").replace("\r", "").replace("\n", "")
            header = (
                f'--{boundary}\r\nContent-Disposition: form-data; name="{safe_name}"'
                "\r\n\r\n"
            ).encode()
            encoded_value = value.encode("utf-8")
            self._segments.append((header + encoded_value + b"\r\n", len(header + encoded_value) + 2))
        safe_file_name = Path(file_name).name.replace('"', "%22").replace("\r", "").replace("\n", "")
        file_header = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{field_name}"; filename="{safe_file_name}"\r\n'
            f"Content-Type: {file_mime_type}\r\n\r\n"
        ).encode()
        file_size = file_path.stat().st_size
        self._segments.append((file_header, len(file_header)))
        self._segments.append((file_path, file_size))
        self._segments.append((f"\r\n--{boundary}--\r\n".encode(), len(f"\r\n--{boundary}--\r\n")))
        self.length = sum(size for _, size in self._segments)
        self._segment_index = 0
        self._segment_offset = 0
        self._file: BinaryIO | None = None
        self._closed = False

    def __len__(self) -> int:
        return self.length

    def read(self, size: int = -1) -> bytes:
        if self._closed:
            return b""
        remaining_output = size if size >= 0 else self.length
        chunks: list[bytes] = []
        while remaining_output > 0 and self._segment_index < len(self._segments):
            source, source_size = self._segments[self._segment_index]
            if self._segment_offset >= source_size:
                self._advance_segment()
                continue
            wanted = min(remaining_output, source_size - self._segment_offset)
            if isinstance(source, Path):
                if self._file is None:
                    self._file = source.open("rb")
                chunk = self._file.read(wanted)
            else:
                chunk = source[self._segment_offset : self._segment_offset + wanted]
            if not chunk:
                raise WorkflowError("multipart_read_error", "读取上传文件失败")
            chunks.append(chunk)
            self._segment_offset += len(chunk)
            remaining_output -= len(chunk)
        if self._segment_index >= len(self._segments):
            self.close()
        return b"".join(chunks)

    def _advance_segment(self) -> None:
        if isinstance(self._segments[self._segment_index][0], Path) and self._file is not None:
            self._file.close()
            self._file = None
        self._segment_index += 1
        self._segment_offset = 0

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None
        self._closed = True

    def __del__(self) -> None:
        self.close()


class HTTPClient:
    """Small standard-library HTTP client with bounded response handling."""

    def __init__(self, *, secrets_to_redact: Sequence[str] = ()) -> None:
        self._secrets = tuple(secret for secret in secrets_to_redact if secret)

    def _open(self, request: Request, timeout_seconds: float):
        try:
            return urlopen(request, timeout=timeout_seconds)
        except HTTPError as exc:
            raise WorkflowError("http_error", _error_detail(exc, self._secrets)) from exc
        except (URLError, TimeoutError) as exc:
            reason = getattr(exc, "reason", exc)
            raise WorkflowError(
                "network_error",
                _redact(f"网络请求失败：{reason}", self._secrets),
            ) from exc

    @staticmethod
    def _decode_json(response, label: str) -> dict[str, Any]:
        raw = response.read(MAX_JSON_BYTES + 1)
        if len(raw) > MAX_JSON_BYTES:
            raise WorkflowError("response_too_large", f"{label}响应超过 2 MiB")
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WorkflowError("invalid_json", f"{label}未返回有效 JSON") from exc
        if not isinstance(payload, dict):
            raise WorkflowError("invalid_json", f"{label} JSON 顶层必须是对象")
        return payload

    def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        json_body: Mapping[str, Any] | None = None,
        timeout_seconds: float = 60,
    ) -> dict[str, Any]:
        request_headers = {"Accept": "application/json", **dict(headers or {})}
        data = None
        if json_body is not None:
            data = json.dumps(json_body, ensure_ascii=False, separators=(",", ":")).encode()
            request_headers["Content-Type"] = "application/json"
        request = Request(url, data=data, headers=request_headers, method=method)
        with self._open(request, timeout_seconds) as response:
            status = getattr(response, "status", response.getcode())
            if not 200 <= status < 300:
                raise WorkflowError("http_error", f"HTTP {status}")
            return self._decode_json(response, "服务")

    def upload_multipart(
        self,
        url: str,
        *,
        token: str,
        fields: Mapping[str, str],
        file_path: Path,
        timeout_seconds: float = 120,
    ) -> dict[str, Any]:
        mime_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        body = _MultipartBody(
            fields=fields,
            file_path=file_path,
            field_name="file",
            file_name=file_path.name,
            file_mime_type=mime_type,
        )
        request = Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "Content-Type": body.content_type,
                "Content-Length": str(len(body)),
            },
            method="POST",
        )
        try:
            with self._open(request, timeout_seconds) as response:
                status = getattr(response, "status", response.getcode())
                if not 200 <= status < 300:
                    raise WorkflowError("http_error", f"资产上传返回 HTTP {status}")
                return self._decode_json(response, "资产上传")
        finally:
            body.close()

    def fetch_to_file(
        self,
        url: str,
        destination: Path,
        *,
        headers: Mapping[str, str] | None,
        max_bytes: int,
        timeout_seconds: float,
    ) -> FetchMetadata:
        request = Request(url, headers=dict(headers or {}), method="GET")
        destination.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        size = 0
        try:
            with self._open(request, timeout_seconds) as response:
                status = getattr(response, "status", response.getcode())
                if status != 200:
                    raise WorkflowError("download_http_error", f"视频下载返回 HTTP {status}")
                content_type = (response.headers.get("Content-Type") or "").split(";", 1)[0]
                content_length_raw = response.headers.get("Content-Length")
                try:
                    content_length = int(content_length_raw) if content_length_raw is not None else None
                except ValueError as exc:
                    raise WorkflowError("invalid_content_length", "视频响应 Content-Length 无效") from exc
                if content_length is not None and content_length > max_bytes:
                    raise WorkflowError("download_too_large", "视频超过允许的下载大小")
                with destination.open("wb") as output:
                    while chunk := response.read(DOWNLOAD_CHUNK_SIZE):
                        size += len(chunk)
                        if size > max_bytes:
                            raise WorkflowError("download_too_large", "视频超过允许的下载大小")
                        output.write(chunk)
                        digest.update(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                return FetchMetadata(
                    mime_type=content_type.strip().lower(),
                    content_length=content_length,
                    size_bytes=size,
                    sha256=digest.hexdigest(),
                )
        except Exception:
            destination.unlink(missing_ok=True)
            raise


def _file_sha256(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(DOWNLOAD_CHUNK_SIZE):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _rate(value: Any) -> float | None:
    if not isinstance(value, str) or "/" not in value:
        return None
    try:
        rate = float(Fraction(value))
    except (ValueError, ZeroDivisionError):
        return None
    return round(rate, 3) if rate > 0 else None


def parse_ffprobe_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    format_info = payload.get("format")
    streams = payload.get("streams")
    if not isinstance(format_info, Mapping) or not isinstance(streams, list):
        raise WorkflowError("media_probe_invalid", "ffprobe 未返回媒体容器和流信息")
    format_names = {
        item.strip().lower()
        for item in str(format_info.get("format_name", "")).split(",")
        if item.strip()
    }
    if "mp4" not in format_names:
        raise WorkflowError("mp4_container_invalid", "下载文件不是可识别的 MP4 容器")
    video_streams = [
        stream
        for stream in streams
        if isinstance(stream, Mapping) and stream.get("codec_type") == "video"
    ]
    if not video_streams:
        raise WorkflowError("video_stream_missing", "下载文件没有视频流")
    video = video_streams[0]
    if str(video.get("codec_name", "")).lower() != "h264":
        raise WorkflowError("video_codec_invalid", "DMC 视频视频流不是 H.264")
    width, height = video.get("width"), video.get("height")
    if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
        raise WorkflowError("video_dimensions_invalid", "视频宽高无效")
    audio_streams = [
        stream
        for stream in streams
        if isinstance(stream, Mapping) and stream.get("codec_type") == "audio"
    ]
    duration_raw = format_info.get("duration")
    try:
        duration_seconds = round(float(duration_raw), 3)
    except (TypeError, ValueError):
        duration_seconds = None
    frames_raw = video.get("nb_frames")
    try:
        frame_count = int(frames_raw) if frames_raw is not None else None
    except (TypeError, ValueError):
        frame_count = None
    return {
        "container": "mp4",
        "video_codec": "h264",
        "width": width,
        "height": height,
        "fps": _rate(video.get("avg_frame_rate")) or _rate(video.get("r_frame_rate")),
        "frame_count": frame_count,
        "duration_seconds": duration_seconds,
        "audio_codec": (
            str(audio_streams[0].get("codec_name")) if audio_streams else None
        ),
        "audio_channels": (
            audio_streams[0].get("channels") if audio_streams else None
        ),
    }


def probe_video(path: Path, *, ffprobe_bin: str = "ffprobe") -> dict[str, Any]:
    command = [
        ffprobe_bin,
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        str(path),
    ]
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise WorkflowError("ffprobe_unavailable", "无法执行 ffprobe 校验视频") from exc
    if result.returncode != 0:
        detail = _redact((result.stderr or "ffprobe 校验失败").strip()[:500], ())
        raise WorkflowError("media_probe_failed", detail)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise WorkflowError("media_probe_invalid", "ffprobe 输出不是有效 JSON") from exc
    if not isinstance(payload, dict):
        raise WorkflowError("media_probe_invalid", "ffprobe 输出顶层必须是对象")
    return parse_ffprobe_payload(payload)


def download_video(
    video_url: str,
    *,
    dmc_base_url: str,
    dmc_api_key: str,
    output_path: Path,
    overwrite: bool,
    http: HTTPClient,
    probe_runner: Callable[..., dict[str, Any]] = probe_video,
    ffprobe_bin: str = "ffprobe",
    timeout_seconds: float = 180,
    max_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    if output_path.exists() and not overwrite:
        raise WorkflowError(
            "output_exists",
            f"输出文件已存在：{output_path}；确认替换后使用 --overwrite",
        )
    resolved_url = resolve_dmc_url(dmc_base_url, video_url)
    headers: dict[str, str] = {"Accept": "video/mp4"}
    if _same_origin(resolved_url, dmc_base_url):
        headers["Authorization"] = f"Bearer {dmc_api_key}"
    part_path = output_path.with_name(f"{output_path.name}.part")
    part_path.unlink(missing_ok=True)
    try:
        fetched = http.fetch_to_file(
            resolved_url,
            part_path,
            headers=headers,
            max_bytes=max_bytes,
            timeout_seconds=timeout_seconds,
        )
        if fetched.mime_type != "video/mp4":
            raise WorkflowError(
                "mime_mismatch",
                f"视频 MIME 不是 video/mp4：{fetched.mime_type or '<missing>'}",
            )
        if fetched.content_length is None:
            raise WorkflowError("content_length_missing", "视频响应缺少 Content-Length")
        local_size, local_sha256 = _file_sha256(part_path)
        if local_size != fetched.content_length or local_size != fetched.size_bytes:
            raise WorkflowError("length_mismatch", "视频下载长度与响应声明不一致")
        if local_sha256 != fetched.sha256:
            raise WorkflowError("download_hash_mismatch", "视频流式下载 SHA-256 校验失败")
        if expected_sha256 and local_sha256.lower() != expected_sha256.lower():
            raise WorkflowError("expected_hash_mismatch", "视频 SHA-256 与期望值不一致")
        media = probe_runner(part_path, ffprobe_bin=ffprobe_bin)
        if media.get("container") != "mp4":
            raise WorkflowError("mp4_container_invalid", "下载文件不是 MP4 容器")
        if str(media.get("video_codec", "")).lower() != "h264":
            raise WorkflowError("video_codec_invalid", "DMC 视频视频流不是 H.264")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(part_path, output_path)
    except Exception:
        part_path.unlink(missing_ok=True)
        raise
    return {
        "path": str(output_path.resolve()),
        "url": resolved_url,
        "size_bytes": local_size,
        "sha256": local_sha256,
        "media": media,
    }


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def save_checkpoint(
    path: Path,
    *,
    task_id: str,
    status: str,
    status_url: str,
    error_code: str | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "task_id": task_id,
        "status": status,
        "status_url": status_url,
        "updated_at": _utc_now(),
    }
    if error_code:
        payload["error_code"] = error_code
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.part")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _terminal_message(payload: Mapping[str, Any]) -> str:
    for key in ("message", "failure_reason", "error"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:500]
        if isinstance(value, Mapping):
            nested = value.get("message") or value.get("code")
            if nested:
                return str(nested)[:500]
    return ""


def _terminal_error_code(payload: Mapping[str, Any]) -> str | None:
    for key in ("error_code", "code"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:128]
    for key in ("error", "failure"):
        value = payload.get(key)
        if not isinstance(value, Mapping):
            continue
        for nested_key in ("error_code", "code"):
            nested = value.get(nested_key)
            if isinstance(nested, str) and nested.strip():
                return nested.strip()[:128]
    return None


def _validate_task_id(task_id: Any) -> str:
    if not isinstance(task_id, str) or not task_id.strip():
        raise WorkflowError("task_id_missing", "DMC 提交响应缺少非空任务 ID")
    if any(character.isspace() for character in task_id):
        raise WorkflowError("task_id_invalid", "DMC 任务 ID 格式无效")
    return task_id


def poll_task(
    *,
    task_id: str,
    status_url: str,
    http: HTTPClient,
    dmc_api_key: str,
    poll_interval_seconds: float,
    timeout_seconds: float,
    on_status: Callable[[str], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    if poll_interval_seconds < 0 or timeout_seconds < 0:
        raise WorkflowError("invalid_poll_configuration", "轮询间隔和超时不能为负数")
    deadline = clock() + timeout_seconds
    terminal = {"succeeded", "completed", "failed", "cancelled", "expired"}
    pending = {None, "accepted", "queued", "pending", "running", "processing"}
    headers = {"Authorization": f"Bearer {dmc_api_key}"}
    while True:
        payload = http.request_json(
            "GET",
            status_url,
            headers=headers,
            timeout_seconds=max(10, min(60, timeout_seconds or 10)),
        )
        status_value = payload.get("status")
        status = str(status_value).lower() if status_value is not None else "pending"
        if on_status:
            on_status(status)
        if status in {"succeeded", "completed"}:
            content = payload.get("content")
            video_url = content.get("video_url") if isinstance(content, Mapping) else None
            if not isinstance(video_url, str) or not video_url.strip():
                raise WorkflowError("video_url_missing", "DMC 任务成功但响应缺少 content.video_url")
            return payload
        if status in {"failed", "cancelled", "expired"}:
            detail = _terminal_message(payload)
            suffix = f"：{detail}" if detail else ""
            raise WorkflowError(
                f"task_{status}",
                f"DMC 任务 {task_id} 已 {status}{suffix}",
                provider_code=_terminal_error_code(payload),
            )
        if status_value is not None and status not in pending and status not in terminal:
            # New provider states stay pending until the bounded deadline rather than
            # triggering a duplicate submission or an unsupported immediate failure.
            pass
        if clock() >= deadline:
            raise WorkflowError("poll_timeout", f"等待 DMC 任务 {task_id} 超时，可稍后用 --task-id 续查")
        sleep(min(poll_interval_seconds, max(0.0, deadline - clock())))


def _validate_request_body(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise WorkflowError("request_invalid", "DMC 请求 JSON 顶层必须是对象")
    model = payload.get("model")
    if not isinstance(model, str) or not model.strip():
        raise WorkflowError("request_invalid", "DMC 请求缺少非空 model")
    content = payload.get("content")
    if not isinstance(content, list) or not content:
        raise WorkflowError("request_invalid", "DMC 请求 content 必须是非空数组")
    if not all(isinstance(item, dict) and isinstance(item.get("type"), str) for item in content):
        raise WorkflowError("request_invalid", "DMC 请求 content 项必须包含字符串 type")
    return payload


def _load_request(path: Path) -> dict[str, Any]:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise WorkflowError("request_not_found", f"无法读取请求文件：{path}") from exc
    if size > MAX_REQUEST_FILE_BYTES:
        raise WorkflowError("request_too_large", "DMC 请求文件超过 2 MiB")
    try:
        payload = json.loads(path.read_text())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkflowError("request_invalid", "DMC 请求文件不是有效 JSON") from exc
    return _validate_request_body(payload)


def _validated_asset_payload(
    payload: Mapping[str, Any],
    *,
    local_sha256: str,
    local_size: int,
    require_published: bool,
) -> dict[str, Any]:
    asset_id = payload.get("asset_id")
    if not isinstance(asset_id, str) or not asset_id.strip():
        raise WorkflowError("asset_id_missing", "资产库响应缺少 asset_id")
    if payload.get("sha256") != local_sha256 or payload.get("size_bytes") != local_size:
        raise WorkflowError("asset_hash_mismatch", "资产库返回的文件 SHA-256 或长度不一致")
    mime_type = str(payload.get("mime_type", "")).split(";", 1)[0].strip().lower()
    if mime_type != "video/mp4":
        raise WorkflowError("asset_mime_mismatch", "资产库记录的 MIME 不是 video/mp4")
    if require_published:
        public_url = payload.get("public_url")
        if (
            payload.get("verified") is not True
            or not isinstance(public_url, str)
            or not public_url.strip()
            or payload.get("asset_status") != "published"
            or payload.get("visibility") != "public"
        ):
            raise WorkflowError("asset_publish_unverified", "资产未通过公网回读验证，禁止交给 Toonflow")
        _absolute_http_url(public_url, "资产公网 URL")
    return dict(payload)


def upload_and_publish(
    *,
    video_path: Path,
    video_sha256: str,
    asset_library_base_url: str,
    asset_library_token: str,
    http: HTTPClient,
    title: str,
    category: str,
    tags: str,
    description: str,
    project_id: str | None,
    scene_id: str | None,
    timeout_seconds: float = 180,
) -> dict[str, Any]:
    size = video_path.stat().st_size
    fields = {
        "title": title,
        "category": category,
        "tags": tags,
        "description": description,
    }
    if project_id:
        fields["project_id"] = project_id
    if scene_id:
        fields["scene_id"] = scene_id
    upload_url = _join_url(asset_library_base_url, "/api/integration/assets")
    uploaded = http.upload_multipart(
        upload_url,
        token=asset_library_token,
        fields=fields,
        file_path=video_path,
        timeout_seconds=timeout_seconds,
    )
    asset = _validated_asset_payload(
        uploaded,
        local_sha256=video_sha256,
        local_size=size,
        require_published=False,
    )
    asset_id = asset["asset_id"]
    publish_url = _join_url(
        asset_library_base_url,
        f"/api/integration/assets/{quote(asset_id, safe='')}/publish",
    )
    try:
        published = http.request_json(
            "POST",
            publish_url,
            headers={"Authorization": f"Bearer {asset_library_token}"},
            timeout_seconds=timeout_seconds,
        )
        return _validated_asset_payload(
            published,
            local_sha256=video_sha256,
            local_size=size,
            require_published=True,
        )
    except WorkflowError as exc:
        raise WorkflowError(
            exc.code,
            f"资产 {asset_id} 已上传但未发布：{exc.message}",
        ) from exc


def run_workflow(
    *,
    output_path: Path,
    dmc_base_url: str,
    dmc_api_key: str,
    asset_library_base_url: str,
    asset_library_token: str,
    request_payload: Mapping[str, Any] | None = None,
    existing_task_id: str | None = None,
    checkpoint_path: Path | None = None,
    dmc_http: HTTPClient,
    asset_http: HTTPClient,
    title: str | None = None,
    category: str = "视频生成",
    tags: str = "",
    description: str = "",
    project_id: str | None = None,
    scene_id: str | None = None,
    overwrite: bool = False,
    poll_interval_seconds: float = 2,
    poll_timeout_seconds: float = DEFAULT_POLL_TIMEOUT_SECONDS,
    request_timeout_seconds: float = 60,
    download_timeout_seconds: float = 180,
    asset_timeout_seconds: float = 180,
    max_download_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES,
    expected_sha256: str | None = None,
    ffprobe_bin: str = "ffprobe",
    probe_runner: Callable[..., dict[str, Any]] = probe_video,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    if bool(request_payload) == bool(existing_task_id):
        raise WorkflowError(
            "task_source_invalid",
            "必须且只能提供新任务请求或已有 --task-id",
        )
    if output_path.suffix.lower() != ".mp4":
        raise WorkflowError("output_invalid", "输出文件必须使用 .mp4 扩展名")
    if output_path.exists() and not overwrite:
        raise WorkflowError("output_exists", f"输出文件已存在：{output_path}")
    checkpoint = checkpoint_path or output_path.with_name(f"{output_path.name}.task.json")
    submit_endpoint = _join_url(dmc_base_url, SUBMIT_PATH)
    task_id: str | None = None
    current_status: str | None = None
    current_error_code: str | None = None
    status_observed = False
    try:
        if existing_task_id:
            task_id = _validate_task_id(existing_task_id)
            if checkpoint.exists():
                try:
                    saved = json.loads(checkpoint.read_text())
                except (OSError, json.JSONDecodeError):
                    saved = {}
                if not isinstance(saved, dict):
                    saved = {}
                if saved.get("task_id") and saved["task_id"] != task_id:
                    raise WorkflowError(
                        "checkpoint_mismatch",
                        "指定 task-id 与检查点记录的 DMC 任务不一致",
                    )
                if isinstance(saved.get("status"), str):
                    current_status = saved["status"]
                if isinstance(saved.get("error_code"), str):
                    current_error_code = saved["error_code"]
        else:
            if checkpoint.exists():
                raise WorkflowError(
                    "checkpoint_exists",
                    f"检查点已存在，禁止重复提交：{checkpoint}；改用 --task-id 续查",
                )
            validated_request = _validate_request_body(request_payload)
            submitted = dmc_http.request_json(
                "POST",
                submit_endpoint,
                headers={"Authorization": f"Bearer {dmc_api_key}"},
                json_body=validated_request,
                timeout_seconds=request_timeout_seconds,
            )
            task_id = _validate_task_id(submitted.get("id"))
            # Persist the billable task ID before the first status request.
            save_checkpoint(
                checkpoint,
                task_id=task_id,
                status="accepted",
                status_url=_join_url(submit_endpoint, task_id),
            )
            current_status = "accepted"
        status_url = _join_url(submit_endpoint, task_id)

        def on_status(status: str) -> None:
            nonlocal current_status, current_error_code, status_observed
            current_status = status
            current_error_code = None
            status_observed = True
            save_checkpoint(
                checkpoint,
                task_id=task_id,
                status=status,
                status_url=status_url,
            )

        status_payload = poll_task(
            task_id=task_id,
            status_url=status_url,
            http=dmc_http,
            dmc_api_key=dmc_api_key,
            poll_interval_seconds=poll_interval_seconds,
            timeout_seconds=poll_timeout_seconds,
            on_status=on_status,
            sleep=sleep,
            clock=clock,
        )
        content = status_payload["content"]
        video_url = content["video_url"]
        downloaded = download_video(
            video_url,
            dmc_base_url=dmc_base_url,
            dmc_api_key=dmc_api_key,
            output_path=output_path,
            overwrite=overwrite,
            http=dmc_http,
            probe_runner=probe_runner,
            ffprobe_bin=ffprobe_bin,
            timeout_seconds=download_timeout_seconds,
            max_bytes=max_download_bytes,
            expected_sha256=expected_sha256,
        )
        published = upload_and_publish(
            video_path=Path(downloaded["path"]),
            video_sha256=downloaded["sha256"],
            asset_library_base_url=asset_library_base_url,
            asset_library_token=asset_library_token,
            http=asset_http,
            title=title or f"DMC 视频 {task_id}",
            category=category,
            tags=tags,
            description=description,
            project_id=project_id,
            scene_id=scene_id,
            timeout_seconds=asset_timeout_seconds,
        )
        save_checkpoint(
            checkpoint,
            task_id=task_id,
            status="completed",
            status_url=status_url,
        )
        return {
            "ok": True,
            "task_id": task_id,
            "output_path": downloaded["path"],
            "size_bytes": downloaded["size_bytes"],
            "sha256": downloaded["sha256"],
            "media": downloaded["media"],
            "asset_id": published["asset_id"],
            "public_url": published["public_url"],
            "verified": True,
            "checkpoint_path": str(checkpoint.resolve()),
            "toonflow": {
                "material_url": published["public_url"],
                "local_import_path": downloaded["path"],
            },
        }
    except WorkflowError as exc:
        if task_id:
            terminal_statuses = {"succeeded", "completed", "failed", "cancelled", "expired"}
            if current_status in terminal_statuses:
                error_code = exc.provider_code or exc.code if status_observed else current_error_code or exc.code
                failed_status = current_status
            else:
                error_code = exc.code
                failed_status = "error"
            try:
                save_checkpoint(
                    checkpoint,
                    task_id=task_id,
                    status=failed_status,
                    status_url=_join_url(submit_endpoint, task_id),
                    error_code=error_code,
                )
            except OSError:
                pass
        raise
    except Exception:
        if task_id:
            terminal_statuses = {"succeeded", "completed", "failed", "cancelled", "expired"}
            failed_status = current_status if current_status in terminal_statuses else "error"
            try:
                save_checkpoint(
                    checkpoint,
                    task_id=task_id,
                    status=failed_status,
                    status_url=_join_url(submit_endpoint, task_id),
                    error_code=current_error_code or "unexpected_error",
                )
            except OSError:
                pass
        raise


def _positive_float(value: str) -> float:
    number = float(value)
    if number < 0:
        raise argparse.ArgumentTypeError("不得为负数")
    return number


def _positive_int(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("必须为正整数")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DMC 视频生成、校验与资产库发布工作流")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--request", type=Path, help="新的 DMC 请求 JSON；只会提交一次")
    source.add_argument("--task-id", help="续查已有 DMC 任务，绝不重新提交")
    parser.add_argument("--output", type=Path, required=True, help="最终 MP4 路径")
    parser.add_argument("--checkpoint", type=Path, help="默认是 <output>.task.json")
    parser.add_argument("--overwrite", action="store_true", help="允许覆盖已有 MP4")
    parser.add_argument("--title", help="资产库标题")
    parser.add_argument("--category", default="视频生成")
    parser.add_argument("--tags", default="")
    parser.add_argument("--description", default="")
    parser.add_argument("--project-id")
    parser.add_argument("--scene-id")
    parser.add_argument("--expected-sha256")
    parser.add_argument(
        "--poll-interval-seconds",
        type=_positive_float,
        default=float(os.environ.get("DMC_POLL_INTERVAL_SECONDS", "2")),
    )
    parser.add_argument(
        "--poll-timeout-seconds",
        type=_positive_float,
        default=float(os.environ.get("DMC_POLL_TIMEOUT_SECONDS", str(DEFAULT_POLL_TIMEOUT_SECONDS))),
    )
    parser.add_argument("--request-timeout-seconds", type=_positive_float, default=60)
    parser.add_argument("--download-timeout-seconds", type=_positive_float, default=180)
    parser.add_argument("--asset-timeout-seconds", type=_positive_float, default=180)
    parser.add_argument(
        "--max-download-bytes",
        type=_positive_int,
        default=DEFAULT_MAX_DOWNLOAD_BYTES,
    )
    parser.add_argument("--ffprobe-bin", default=os.environ.get("FFPROBE_BIN", "ffprobe"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        dmc_api_key = os.environ.get("DMC_API_KEY", "").strip()
        asset_token = os.environ.get("ASSET_LIBRARY_API_TOKEN", "").strip()
        if not dmc_api_key:
            raise WorkflowError("missing_env", "缺少环境变量 DMC_API_KEY")
        if not asset_token:
            raise WorkflowError("missing_env", "缺少环境变量 ASSET_LIBRARY_API_TOKEN")
        if dmc_api_key == asset_token:
            raise WorkflowError("credentials_not_separated", "DMC 与资产库必须使用不同令牌")
        dmc_base_url = os.environ.get("DMC_BASE_URL", DMC_DEFAULT_BASE_URL).strip()
        asset_base_url = os.environ.get(
            "ASSET_LIBRARY_BASE_URL",
            ASSET_LIBRARY_DEFAULT_BASE_URL,
        ).strip()
        _absolute_http_url(dmc_base_url, "DMC BASE URL")
        _absolute_http_url(asset_base_url, "资产库 BASE URL")
        request_payload = _load_request(args.request) if args.request else None
        result = run_workflow(
            output_path=args.output,
            checkpoint_path=args.checkpoint,
            dmc_base_url=dmc_base_url,
            dmc_api_key=dmc_api_key,
            asset_library_base_url=asset_base_url,
            asset_library_token=asset_token,
            request_payload=request_payload,
            existing_task_id=args.task_id,
            dmc_http=HTTPClient(secrets_to_redact=(dmc_api_key,)),
            asset_http=HTTPClient(secrets_to_redact=(asset_token,)),
            title=args.title,
            category=args.category,
            tags=args.tags,
            description=args.description,
            project_id=args.project_id,
            scene_id=args.scene_id,
            overwrite=args.overwrite,
            poll_interval_seconds=args.poll_interval_seconds,
            poll_timeout_seconds=args.poll_timeout_seconds,
            request_timeout_seconds=args.request_timeout_seconds,
            download_timeout_seconds=args.download_timeout_seconds,
            asset_timeout_seconds=args.asset_timeout_seconds,
            max_download_bytes=args.max_download_bytes,
            expected_sha256=args.expected_sha256,
            ffprobe_bin=args.ffprobe_bin,
        )
    except WorkflowError as exc:
        print(
            json.dumps(
                {"ok": False, "error": {"code": exc.code, "message": exc.message}},
                ensure_ascii=False,
            )
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
