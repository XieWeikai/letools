"""Loopback HTTP bridge from a local LeRobot dataset to the web visualizer.

The upstream application reads the Hugging Face ``resolve/main`` URL shape.
This server exposes exactly that shape for one immutable root, plus local
Doctor HTML/JSON endpoints. It is intentionally not a general file server.
"""

from __future__ import annotations

import html
import json
import mimetypes
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit


_COPY_BUFFER = 1024 * 1024


class DoctorReport:
    """Lazily compute and cache all upstream Doctor checks for one dataset."""

    def __init__(self, root: Path, max_episodes: int | None) -> None:
        self.root = root
        self.max_episodes = max_episodes
        self._lock = threading.Lock()
        self._payload: dict[str, Any] | None = None

    def payload(self) -> dict[str, Any]:
        """Return Doctor's canonical JSON representation exactly once."""

        with self._lock:
            if self._payload is None:
                from lerobot_doctor.dataset_loader import load_dataset
                from lerobot_doctor.report import report_to_json
                from lerobot_doctor.runner import run_checks

                dataset = load_dataset(
                    str(self.root), max_episodes=self.max_episodes
                )
                if dataset.info is None:
                    raise ValueError(
                        dataset.info_error or "Doctor could not load dataset"
                    )
                self._payload = json.loads(report_to_json(run_checks(dataset)))
            return self._payload

    def json_bytes(self) -> bytes:
        """Serialize the cached report for automation and debugging."""

        return json.dumps(self.payload(), indent=2).encode("utf-8")

    def html_bytes(self) -> bytes:
        """Render a compact self-contained report suitable for an iframe."""

        payload = self.payload()
        severity = str(payload.get("overall_severity", "PASS"))
        checks = []
        for check in payload.get("checks", []):
            messages = "".join(
                "<li><span class='message-severity'>"
                + html.escape(str(message.get("severity", "")))
                + "</span> "
                + html.escape(str(message.get("message", "")))
                + "</li>"
                for message in check.get("messages", [])
            )
            checks.append(
                "<section><div class='check-head'><h2>"
                + html.escape(str(check.get("name", "Check")))
                + "</h2><span class='severity "
                + html.escape(str(check.get("severity", "PASS")).lower())
                + "'>"
                + html.escape(str(check.get("severity", "PASS")))
                + "</span></div><ul>"
                + messages
                + "</ul></section>"
            )
        sample = (
            "all episodes"
            if self.max_episodes is None
            else f"up to {self.max_episodes} episodes"
        )
        document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport"
content="width=device-width,initial-scale=1"><title>LeRobot Doctor</title>
<style>
:root {{ color-scheme: dark; font-family: Inter, ui-sans-serif, system-ui,
sans-serif; }}
body {{ margin: 0; background: #0b0d10; color: #e5e7eb; }}
header {{ padding: 20px 24px 16px; border-bottom: 1px solid #262a31; }}
h1 {{ margin: 0 0 6px; font-size: 20px; letter-spacing: 0; }}
.meta {{ color: #9ca3af; font-size: 13px; }}
main {{ padding: 16px 24px 32px; display: grid; gap: 10px; }}
section {{ border: 1px solid #2b3038; border-radius: 6px; background: #12151a;
padding: 12px 14px; }}
.check-head {{ display: flex; align-items: center; justify-content: space-between;
gap: 12px; }}
h2 {{ margin: 0; font-size: 14px; letter-spacing: 0; }}
.severity {{ border: 1px solid currentColor; border-radius: 4px; padding: 2px 7px;
font: 700 11px ui-monospace, monospace; }}
.pass {{ color: #52c98b; }} .warn {{ color: #f0b64d; }} .fail {{ color: #ef6b73; }}
ul {{ margin: 9px 0 0; padding-left: 19px; color: #b8bec8; font-size: 12px;
line-height: 1.55; }}
.message-severity {{ color: #7f8998; font: 700 10px ui-monospace, monospace; }}
</style></head><body><header><h1>LeRobot Doctor
<span class="severity {html.escape(severity.lower())}">
{html.escape(severity)}</span></h1>
<div class="meta">{html.escape(str(self.root))} &middot; {html.escape(sample)} &middot;
{html.escape(str(payload.get('total_episodes', '?')))} episodes &middot;
{html.escape(str(payload.get('total_frames', '?')))} frames</div></header>
<main>{''.join(checks)}</main></body></html>"""
        return document.encode("utf-8")


class LocalDatasetServer(ThreadingHTTPServer):
    """Threaded server restricted to one dataset and one route identity."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        root: Path,
        repo_id: str,
        *,
        doctor_max_episodes: int | None,
        quiet: bool = True,
    ) -> None:
        self.dataset_root = root.resolve(strict=True)
        self.repo_id = repo_id
        self.resolve_prefix = f"/{repo_id}/resolve/main/"
        self.doctor_report = DoctorReport(self.dataset_root, doctor_max_episodes)
        self.quiet = quiet
        super().__init__(address, LocalDatasetHandler)


class LocalDatasetHandler(BaseHTTPRequestHandler):
    """Serve GET/HEAD with single-range support and strict path confinement."""

    server: LocalDatasetServer

    def log_message(self, format: str, *args: object) -> None:
        if not self.server.quiet:
            super().log_message(format, *args)

    def end_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Range, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
        super().end_headers()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        self.end_headers()

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch(send_body=False)

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch(send_body=True)

    def _dispatch(self, *, send_body: bool) -> None:
        path = urlsplit(self.path).path
        try:
            if path == "/healthz":
                self._send_bytes(b'{"ok":true}', "application/json", send_body)
                return
            if path in {"/doctor", "/doctor/"}:
                self._send_bytes(
                    self.server.doctor_report.html_bytes(),
                    "text/html; charset=utf-8",
                    send_body,
                )
                return
            if path == "/doctor/report.json":
                self._send_bytes(
                    self.server.doctor_report.json_bytes(),
                    "application/json",
                    send_body,
                )
                return
            target = self._resolve_dataset_path(path)
            if target is None or not target.is_file():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self._send_file(target, send_body=send_body)
        except (OSError, ValueError) as error:
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(error))

    def _resolve_dataset_path(self, request_path: str) -> Path | None:
        if not request_path.startswith(self.server.resolve_prefix):
            return None
        relative = unquote(request_path[len(self.server.resolve_prefix) :])
        candidate = (self.server.dataset_root / relative).resolve(strict=False)
        if not candidate.is_relative_to(self.server.dataset_root):
            return None
        return candidate

    def _send_bytes(self, payload: bytes, content_type: str, send_body: bool) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if send_body:
            self.wfile.write(payload)

    def _range(self, size: int) -> tuple[int, int] | None:
        value = self.headers.get("Range")
        if value is None:
            return None
        if not value.startswith("bytes=") or "," in value:
            raise ValueError("Only one byte range is supported")
        start_text, end_text = value[6:].split("-", 1)
        if not start_text:
            length = int(end_text)
            if length <= 0:
                raise ValueError("Invalid suffix range")
            return max(0, size - length), size - 1
        start = int(start_text)
        end = min(size - 1, int(end_text)) if end_text else size - 1
        if start < 0 or start >= size or end < start:
            raise ValueError("Range is outside the file")
        return start, end

    def _send_file(self, path: Path, *, send_body: bool) -> None:
        size = path.stat().st_size
        try:
            selected = self._range(size)
        except (ValueError, TypeError):
            self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            self.send_header("Content-Range", f"bytes */{size}")
            self.end_headers()
            return
        start, end = selected or (0, max(0, size - 1))
        status = HTTPStatus.PARTIAL_CONTENT if selected is not None else HTTPStatus.OK
        length = end - start + 1 if size else 0
        self.send_response(status)
        self.send_header(
            "Content-Type",
            mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        )
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-cache")
        if selected is not None:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if not send_body or not length:
            return
        with path.open("rb") as source:
            source.seek(start)
            remaining = length
            while remaining:
                chunk = source.read(min(_COPY_BUFFER, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)


__all__ = ["DoctorReport", "LocalDatasetServer"]
