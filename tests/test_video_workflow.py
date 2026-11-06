import hashlib
import importlib.util
import json
import re
import sys
from email import policy
from email.parser import BytesParser
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "backend" / "scripts" / "video_workflow.py"
SPEC = importlib.util.spec_from_file_location("video_workflow", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise ImportError(f"Cannot load video workflow module from {MODULE_PATH}")
video_workflow = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = video_workflow
SPEC.loader.exec_module(video_workflow)


VIDEO_BYTES = b"\x00\x01mock mp4 bytes\xff\x00"
VALID_PROBE_PAYLOAD = {
    "format": {"format_name": "mov,mp4", "duration": "2.5"},
    "streams": [
        {
            "codec_type": "video",
            "codec_name": "h264",
            "width": 1920,
            "height": 1080,
            "avg_frame_rate": "24/1",
            "nb_frames": "60",
        }
    ],
}


class JsonResponse:
    status = 200

    def __init__(self, payload):
        self.headers = {}
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def getcode(self):
        return self.status

    def read(self, size=-1):
        if size < 0:
            body, self._body = self._body, b""
        else:
            body, self._body = self._body[:size], self._body[size:]
        return body


class FakeDmcHTTP:
    def __init__(self, checkpoint_path, statuses=None, *, missing_video_url=False):
        self.checkpoint_path = checkpoint_path
        self.statuses = list(statuses or [{"status": "succeeded", "content": {"video_url": "/video.mp4"}}])
        self.missing_video_url = missing_video_url
        self.calls = []

    def request_json(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if method == "POST":
            return {"id": "task-new"}
        if self.checkpoint_path.exists():
            checkpoint = json.loads(self.checkpoint_path.read_text())
            assert checkpoint["task_id"] in {"task-new", "task-existing"}
            if (
                sum(call[0] == "GET" for call in self.calls) == 1
                and any(call[0] == "POST" for call in self.calls[:-1])
            ):
                assert checkpoint["status"] == "accepted"
        if not self.statuses:
            raise AssertionError("Unexpected extra status request")
        payload = self.statuses.pop(0)
        if self.missing_video_url and payload.get("status") == "succeeded":
            return {"status": "succeeded", "content": {}}
        return payload

    def fetch_to_file(self, url, destination, **kwargs):
        destination.write_bytes(VIDEO_BYTES)
        return video_workflow.FetchMetadata(
            mime_type="video/mp4",
            content_length=len(VIDEO_BYTES),
            size_bytes=len(VIDEO_BYTES),
            sha256=hashlib.sha256(VIDEO_BYTES).hexdigest(),
        )


class FakeAssetHTTP:
    def __init__(self):
        self.calls = []

    def upload_multipart(self, url, *, token, fields, file_path, timeout_seconds):
        self.calls.append(("upload", url, fields, file_path.read_bytes()))
        return {
            "asset_id": "asset-42",
            "sha256": hashlib.sha256(file_path.read_bytes()).hexdigest(),
            "size_bytes": file_path.stat().st_size,
            "mime_type": "video/mp4",
        }

    def request_json(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return {
            "asset_id": "asset-42",
            "sha256": hashlib.sha256(VIDEO_BYTES).hexdigest(),
            "size_bytes": len(VIDEO_BYTES),
            "mime_type": "video/mp4",
            "verified": True,
            "public_url": "https://assets.example/asset-42.mp4",
            "asset_status": "published",
            "visibility": "public",
        }


def run_kwargs(tmp_path, dmc_http, asset_http=None, **overrides):
    kwargs = {
        "output_path": tmp_path / "result.mp4",
        "dmc_base_url": "https://dmc.example/seedance",
        "dmc_api_key": "dmc-test-token",
        "asset_library_base_url": "https://assets.example",
        "asset_library_token": "asset-test-token",
        "request_payload": {"model": "video-model", "content": [{"type": "text", "text": "测试"}]},
        "checkpoint_path": tmp_path / "result.mp4.task.json",
        "dmc_http": dmc_http,
        "asset_http": asset_http or FakeAssetHTTP(),
        "poll_interval_seconds": 0,
        "poll_timeout_seconds": 10,
        "probe_runner": lambda path, **options: video_workflow.parse_ffprobe_payload(VALID_PROBE_PAYLOAD),
    }
    kwargs.update(overrides)
    return kwargs


def test_multipart_stream_has_exact_length_and_preserves_fields_file_and_boundary(tmp_path, monkeypatch):
    video_path = tmp_path / "源视频.mp4"
    file_bytes = bytes(range(256)) * 3 + "尾部".encode()
    video_path.write_bytes(file_bytes)
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        body_stream = request.data
        captured["stream"] = body_stream
        chunks = []
        chunk_sizes = (1, 2, 7, 3, 11, 5)
        index = 0
        while True:
            chunk = body_stream.read(chunk_sizes[index % len(chunk_sizes)])
            if not chunk:
                break
            chunks.append(chunk)
            index += 1
        captured["body"] = b"".join(chunks)
        return JsonResponse({"ok": True})

    monkeypatch.setattr(video_workflow, "urlopen", fake_urlopen)
    response = video_workflow.HTTPClient().upload_multipart(
        "https://assets.example/api/integration/assets",
        token="asset-token",
        fields={"title": "海边日落", "description": "第一版"},
        file_path=video_path,
    )

    request = captured["request"]
    content_type = request.get_header("Content-type")
    match = re.fullmatch(r"multipart/form-data; boundary=(mov-asset-[0-9a-f]{32})", content_type)
    assert match is not None
    body = captured["body"]
    assert int(request.get_header("Content-length")) == len(body)
    assert response == {"ok": True}
    assert captured["stream"]._file is None
    assert captured["stream"]._closed

    envelope = (
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("ascii") + body
    )
    multipart = BytesParser(policy=policy.default).parsebytes(envelope)
    assert multipart.is_multipart()
    parts = {
        part.get_param("name", header="content-disposition"): part
        for part in multipart.iter_parts()
    }
    assert parts["title"].get_payload(decode=True).decode("utf-8") == "海边日落"
    assert parts["description"].get_payload(decode=True).decode("utf-8") == "第一版"
    assert parts["file"].get_filename() == video_path.name
    assert parts["file"].get_payload(decode=True) == file_bytes


def test_run_workflow_completes_submit_download_upload_and_publish(tmp_path):
    checkpoint_path = tmp_path / "result.mp4.task.json"
    dmc_http = FakeDmcHTTP(checkpoint_path)
    asset_http = FakeAssetHTTP()

    result = video_workflow.run_workflow(
        **run_kwargs(tmp_path, dmc_http, asset_http, title="成片")
    )

    assert [call[0] for call in dmc_http.calls] == ["POST", "GET"]
    assert dmc_http.calls[0][2]["json_body"]["model"] == "video-model"
    assert json.loads(checkpoint_path.read_text())["status"] == "completed"
    assert (tmp_path / "result.mp4").read_bytes() == VIDEO_BYTES
    assert result["ok"] is True
    assert result["task_id"] == "task-new"
    assert result["asset_id"] == "asset-42"
    assert result["public_url"] == "https://assets.example/asset-42.mp4"
    assert result["media"]["video_codec"] == "h264"
    assert [call[0] for call in asset_http.calls] == ["upload", "POST"]
    assert asset_http.calls[0][2]["title"] == "成片"
    assert asset_http.calls[0][3] == VIDEO_BYTES


def test_existing_task_id_never_posts_a_new_task(tmp_path):
    checkpoint_path = tmp_path / "result.mp4.task.json"
    dmc_http = FakeDmcHTTP(checkpoint_path, missing_video_url=True)
    kwargs = run_kwargs(
        tmp_path,
        dmc_http,
        request_payload=None,
        existing_task_id="task-existing",
    )

    with pytest.raises(video_workflow.WorkflowError) as error:
        video_workflow.run_workflow(**kwargs)

    assert error.value.code == "video_url_missing"
    assert [call[0] for call in dmc_http.calls] == ["GET"]


@pytest.mark.parametrize(
    ("status", "expected_code"),
    [("failed", "task_failed"), ("cancelled", "task_cancelled")],
)
def test_terminal_failure_checkpoint_keeps_status_and_actual_code(
    tmp_path, status, expected_code
):
    checkpoint_path = tmp_path / "result.mp4.task.json"
    checkpoint_path.write_text(
        json.dumps({"task_id": "task-existing", "status": "accepted"}), encoding="utf-8"
    )
    dmc_http = FakeDmcHTTP(checkpoint_path, [{"status": status, "error": {"code": "provider"}}])
    kwargs = run_kwargs(
        tmp_path,
        dmc_http,
        request_payload=None,
        existing_task_id="task-existing",
    )

    with pytest.raises(video_workflow.WorkflowError) as error:
        video_workflow.run_workflow(**kwargs)

    checkpoint = json.loads(checkpoint_path.read_text())
    assert error.value.code == expected_code
    assert checkpoint["status"] == status
    assert checkpoint["error_code"] == "provider"


def test_unexpected_download_error_keeps_completed_supplier_status_in_checkpoint(tmp_path):
    checkpoint_path = tmp_path / "result.mp4.task.json"
    dmc_http = FakeDmcHTTP(checkpoint_path)

    def fail_download(url, destination, **kwargs):
        raise OSError("disk full")

    dmc_http.fetch_to_file = fail_download
    kwargs = run_kwargs(tmp_path, dmc_http)

    with pytest.raises(OSError, match="disk full"):
        video_workflow.run_workflow(**kwargs)

    checkpoint = json.loads(checkpoint_path.read_text())
    assert checkpoint["status"] == "succeeded"
    assert checkpoint["error_code"] == "unexpected_error"


def test_poll_timeout_is_bounded_and_reports_poll_timeout():
    class Clock:
        now = 0

        def sleep(self, seconds):
            self.now += seconds

        def __call__(self):
            return self.now

    class PendingHTTP:
        calls = 0

        def request_json(self, method, url, **kwargs):
            self.calls += 1
            return {"status": "running"}

    clock = Clock()
    http = PendingHTTP()
    with pytest.raises(video_workflow.WorkflowError) as error:
        video_workflow.poll_task(
            task_id="task-1",
            status_url="https://dmc.example/task-1",
            http=http,
            dmc_api_key="token",
            poll_interval_seconds=1,
            timeout_seconds=2,
            sleep=clock.sleep,
            clock=clock,
        )

    assert error.value.code == "poll_timeout"
    assert http.calls == 3
    assert clock.now == 2


def test_download_rejects_mime_and_hash_mismatches(tmp_path):
    class DownloadHTTP:
        def __init__(self, mime_type="video/mp4", sha256=None):
            self.mime_type = mime_type
            self.sha256 = sha256 or hashlib.sha256(VIDEO_BYTES).hexdigest()

        def fetch_to_file(self, url, destination, **kwargs):
            destination.write_bytes(VIDEO_BYTES)
            return video_workflow.FetchMetadata(
                mime_type=self.mime_type,
                content_length=len(VIDEO_BYTES),
                size_bytes=len(VIDEO_BYTES),
                sha256=self.sha256,
            )

    cases = [
        (DownloadHTTP(mime_type="application/octet-stream"), None, "mime_mismatch"),
        (DownloadHTTP(sha256="0" * 64), None, "download_hash_mismatch"),
        (DownloadHTTP(), "0" * 64, "expected_hash_mismatch"),
    ]
    for index, (http, expected_sha256, expected_code) in enumerate(cases):
        output = tmp_path / f"invalid-{index}.mp4"
        with pytest.raises(video_workflow.WorkflowError) as error:
            video_workflow.download_video(
                "/video.mp4",
                dmc_base_url="https://dmc.example",
                dmc_api_key="token",
                output_path=output,
                overwrite=False,
                http=http,
                expected_sha256=expected_sha256,
            )
        assert error.value.code == expected_code
        assert not output.exists()
        assert not output.with_name(f"{output.name}.part").exists()


@pytest.mark.parametrize(
    ("payload", "expected_code"),
    [
        (
            {"format": {"format_name": "mov"}, "streams": VALID_PROBE_PAYLOAD["streams"]},
            "mp4_container_invalid",
        ),
        (
            {
                "format": {"format_name": "mov,mp4"},
                "streams": [{**VALID_PROBE_PAYLOAD["streams"][0], "codec_name": "mpeg4"}],
            },
            "video_codec_invalid",
        ),
    ],
)
def test_download_rejects_invalid_container_or_non_h264(tmp_path, payload, expected_code):
    class DownloadHTTP:
        def fetch_to_file(self, url, destination, **kwargs):
            destination.write_bytes(VIDEO_BYTES)
            return video_workflow.FetchMetadata(
                mime_type="video/mp4",
                content_length=len(VIDEO_BYTES),
                size_bytes=len(VIDEO_BYTES),
                sha256=hashlib.sha256(VIDEO_BYTES).hexdigest(),
            )

    def probe_runner(path, **kwargs):
        return video_workflow.parse_ffprobe_payload(payload)

    output = tmp_path / "invalid-video.mp4"
    with pytest.raises(video_workflow.WorkflowError) as error:
        video_workflow.download_video(
            "/video.mp4",
            dmc_base_url="https://dmc.example",
            dmc_api_key="token",
            output_path=output,
            overwrite=False,
            http=DownloadHTTP(),
            probe_runner=probe_runner,
        )

    assert error.value.code == expected_code
    assert not output.exists()
