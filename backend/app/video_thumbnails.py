import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .thumbnails import THUMBNAIL_MAX_EDGE, ThumbnailResult, generate_image_thumbnail

FFPROBE_TIMEOUT_SECONDS = 20
FFMPEG_TIMEOUT_SECONDS = 60


@dataclass(frozen=True)
class VideoMetadata:
    width: int
    height: int
    duration_ms: int | None


@dataclass(frozen=True)
class VideoThumbnailResult:
    content: ThumbnailResult
    source_width: int
    source_height: int
    duration_ms: int | None


def _positive_int(value: object) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _duration_ms(value: object) -> int | None:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    return round(seconds * 1000)


def _rotation_degrees(stream: dict) -> int:
    candidates = [stream.get("tags", {}).get("rotate")]
    candidates.extend(item.get("rotation") for item in stream.get("side_data_list", []))
    for candidate in candidates:
        try:
            return round(float(candidate))
        except (TypeError, ValueError):
            continue
    return 0


def probe_video(source_path: Path) -> VideoMetadata | None:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None

    try:
        completed = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(source_path),
            ],
            capture_output=True,
            check=False,
            text=True,
            timeout=FFPROBE_TIMEOUT_SECONDS,
        )
        if completed.returncode != 0:
            return None
        payload = json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return None

    streams = payload.get("streams", [])
    stream = next(
        (
            item
            for item in streams
            if item.get("codec_type") == "video"
            and not item.get("disposition", {}).get("attached_pic")
        ),
        None,
    )
    if not stream:
        return None

    width = _positive_int(stream.get("width"))
    height = _positive_int(stream.get("height"))
    if not width or not height:
        return None
    if abs(_rotation_degrees(stream)) % 180 == 90:
        width, height = height, width

    duration_ms = _duration_ms(stream.get("duration"))
    if duration_ms is None:
        duration_ms = _duration_ms(payload.get("format", {}).get("duration"))
    return VideoMetadata(width=width, height=height, duration_ms=duration_ms)


def _scaled_dimensions(width: int, height: int) -> tuple[int, int]:
    scale = min(1.0, THUMBNAIL_MAX_EDGE / max(width, height))
    return max(1, round(width * scale)), max(1, round(height * scale))


def generate_video_thumbnail(source_path: Path, frame_path: Path) -> VideoThumbnailResult | None:
    """Extract the first decodable video frame and return a bounded WebP preview."""
    ffmpeg = shutil.which("ffmpeg")
    metadata = probe_video(source_path)
    if not ffmpeg or not metadata:
        return None

    width, height = _scaled_dimensions(metadata.width, metadata.height)
    try:
        completed = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-y",
                "-i",
                str(source_path),
                "-map",
                "0:v:0",
                "-frames:v",
                "1",
                "-vf",
                f"scale={width}:{height}:flags=lanczos,setsar=1",
                "-an",
                "-c:v",
                "png",
                str(frame_path),
            ],
            capture_output=True,
            check=False,
            timeout=FFMPEG_TIMEOUT_SECONDS,
        )
        if completed.returncode != 0 or not frame_path.is_file():
            return None
        with frame_path.open("rb") as frame:
            thumbnail = generate_image_thumbnail(frame)
    except (OSError, subprocess.SubprocessError):
        return None
    if not thumbnail:
        return None
    return VideoThumbnailResult(
        content=thumbnail,
        source_width=metadata.width,
        source_height=metadata.height,
        duration_ms=metadata.duration_ms,
    )
