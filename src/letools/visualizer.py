"""Lifecycle and local-data adapters for the vendored LeRobot Visualizer.

The upstream Next.js application remains an immutable external snapshot. This
module prepares a patched user-cache copy, installs its locked Bun dependencies,
and supervises the web process plus optional data and annotation services.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from letools.external import upstream_project, visualizer_patches, visualizer_source
from letools.validation import validate_dataset
from letools.visualizer_server import LocalDatasetServer


@dataclass(frozen=True)
class VisualizerInstallation:
    """Prepared application cache and dependency identity."""

    source: Path
    upstream_commit: str
    fingerprint: str
    bun: str | None
    bun_version: str | None
    dependencies_installed: bool


@dataclass(frozen=True)
class VisualizerTarget:
    """Resolved local path or Hugging Face repository identity."""

    requested: str
    repo_id: str
    route: str
    local_root: Path | None = None

    @property
    def is_local(self) -> bool:
        return self.local_root is not None


@dataclass(frozen=True)
class VisualizerConfig:
    """Static launch configuration; no runtime worker adjustment is needed."""

    host: str = "127.0.0.1"
    port: int = 3000
    data_port: int = 8765
    annotation_port: int = 7861
    public_data_url: str | None = None
    public_annotation_url: str | None = None
    annotations: bool = True
    doctor_max_episodes: int | None = 20
    production: bool = False
    open_browser: bool = False
    cache_dir: Path | None = None
    bun: str | None = None
    force_setup: bool = False


def _default_cache() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    commit = upstream_project("lerobot-dataset-visualizer")["commit"]
    return base / "letools/visualizer" / commit


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_fingerprint() -> str:
    digest = hashlib.sha256()
    project = upstream_project("lerobot-dataset-visualizer")
    digest.update(project["commit"].encode())
    digest.update(_digest(visualizer_source() / "bun.lock").encode())
    for patch in visualizer_patches():
        digest.update(patch.name.encode())
        digest.update(_digest(patch).encode())
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _prepare_source(cache: Path, *, force: bool) -> tuple[Path, str]:
    """Create a pristine, patched cache copy without touching the checkout."""

    fingerprint = _source_fingerprint()
    destination = cache / "source"
    marker = cache / ".letools-source.json"
    existing = _read_json(marker)
    if (
        not force
        and destination.is_dir()
        and existing
        and existing.get("fingerprint") == fingerprint
    ):
        return destination, fingerprint

    cache.mkdir(parents=True, exist_ok=True)
    staging = cache / f".source-staging-{uuid.uuid4().hex}"
    stale: Path | None = None
    try:
        shutil.copytree(
            visualizer_source(),
            staging,
            ignore=shutil.ignore_patterns(
                ".git", "node_modules", ".next", "out", "build"
            ),
        )
        for patch in visualizer_patches():
            try:
                subprocess.run(
                    ["git", "apply", "--whitespace=nowarn", str(patch)],
                    cwd=staging,
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except subprocess.CalledProcessError as error:
                detail = error.stderr.strip() or error.stdout.strip()
                raise RuntimeError(
                    f"Visualizer integration patch failed ({patch.name}): {detail}"
                ) from error
        marker_payload = {
            "upstream_commit": upstream_project("lerobot-dataset-visualizer")[
                "commit"
            ],
            "fingerprint": fingerprint,
            "patches": [patch.name for patch in visualizer_patches()],
        }
        if destination.exists():
            stale = cache / f".source-stale-{uuid.uuid4().hex}"
            destination.replace(stale)
        staging.replace(destination)
        marker.write_text(json.dumps(marker_payload, indent=2), encoding="utf-8")
        if stale is not None:
            shutil.rmtree(stale)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        if stale is not None and stale.exists() and not destination.exists():
            stale.replace(destination)
        raise
    return destination, fingerprint


def _find_bun(explicit: str | None) -> str:
    candidate = explicit or shutil.which("bun")
    if candidate is None:
        raise RuntimeError(
            "Bun is required for the Visualizer. Install Bun, then run "
            "`letools visualizer setup`, or pass `--bun /path/to/bun`."
        )
    if explicit:
        path = Path(candidate).expanduser().resolve(strict=False)
        if not path.is_file():
            raise FileNotFoundError(f"Bun executable does not exist: {path}")
        return str(path)
    return candidate


def prepare_visualizer(
    *,
    cache_dir: Path | None = None,
    bun: str | None = None,
    force: bool = False,
    install_dependencies: bool = True,
) -> VisualizerInstallation:
    """Prepare the patched source and optionally install the locked JS graph."""

    cache = (cache_dir or _default_cache()).expanduser().resolve(strict=False)
    source, fingerprint = _prepare_source(cache, force=force)
    if not install_dependencies:
        return VisualizerInstallation(
            source=source,
            upstream_commit=upstream_project("lerobot-dataset-visualizer")["commit"],
            fingerprint=fingerprint,
            bun=None,
            bun_version=None,
            dependencies_installed=(source / "node_modules").is_dir(),
        )

    executable = _find_bun(bun)
    bun_version = subprocess.run(
        [executable, "--version"], check=True, capture_output=True, text=True
    ).stdout.strip()
    dependency_marker = cache / ".letools-dependencies.json"
    expected = {
        "fingerprint": fingerprint,
        "bun_version": bun_version,
        "lock_sha256": _digest(source / "bun.lock"),
    }
    installed = _read_json(dependency_marker)
    if force or installed != expected or not (source / "node_modules").is_dir():
        subprocess.run(
            [executable, "install", "--frozen-lockfile"], cwd=source, check=True
        )
        dependency_marker.write_text(json.dumps(expected, indent=2), encoding="utf-8")
    return VisualizerInstallation(
        source=source,
        upstream_commit=upstream_project("lerobot-dataset-visualizer")["commit"],
        fingerprint=fingerprint,
        bun=executable,
        bun_version=bun_version,
        dependencies_installed=True,
    )


def resolve_visualizer_target(target: str) -> VisualizerTarget:
    """Resolve an existing local directory or an ``org/dataset`` Hub ID."""

    candidate = Path(target).expanduser()
    if candidate.exists():
        root = candidate.resolve(strict=True)
        if not root.is_dir():
            raise ValueError(f"Visualizer local target must be a directory: {root}")
        report = validate_dataset(root, deep=False)
        if not report.valid:
            raise ValueError(
                "Invalid local LeRobot dataset: " + "; ".join(report.errors)
            )
        slug = re.sub(r"[^A-Za-z0-9._-]+", "-", root.name).strip("-.") or "dataset"
        suffix = hashlib.sha256(str(root).encode()).hexdigest()[:8]
        repo_id = f"local/{slug[:48]}-{suffix}"
        return VisualizerTarget(target, repo_id, f"/{repo_id}/0", root)
    if target.count("/") == 1 and not target.startswith(("/", ".", "~")):
        organization, dataset = target.split("/", 1)
        if organization and dataset:
            return VisualizerTarget(target, target, f"/{target}/0")
    raise FileNotFoundError(
        "Target is neither an existing local directory nor an org/dataset "
        f"Hub ID: {target}"
    )


def _load_annotation_backend(
    application_root: Path,
    local_mapping: tuple[str, Path] | None,
) -> Any:
    """Load the upstream FastAPI app and adapt one local alias in memory."""

    path = application_root / "backend/app.py"
    name = f"_letools_visualizer_backend_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load Visualizer annotation backend: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    if local_mapping is not None:
        repo_id, local_root = local_mapping
        original = module._ensure_state

        def ensure_state(request: Any) -> Any:
            if getattr(request, "repo_id", None) == repo_id:
                request = module.DatasetRef(local_path=str(local_root))
            return original(request)

        module._ensure_state = ensure_state
    return module.app


def _free_port(host: str, requested: int) -> int:
    if requested:
        return requested
    with socket.socket() as probe:
        probe.bind((host, 0))
        return int(probe.getsockname()[1])


def _wait_for_http(url: str, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=0.5) as response:
                if response.status < 500:
                    return
        except (OSError, URLError):
            time.sleep(0.05)
    raise TimeoutError(f"Service did not become ready: {url}")


def _browser_host(bind_host: str) -> str:
    return "127.0.0.1" if bind_host in {"0.0.0.0", "::"} else bind_host


def _start_annotation_server(
    application_root: Path,
    host: str,
    port: int,
    local_mapping: tuple[str, Path] | None,
) -> tuple[Any, threading.Thread]:
    import uvicorn

    app = _load_annotation_backend(application_root, local_mapping)
    server = uvicorn.Server(
        uvicorn.Config(app, host=host, port=port, log_level="warning", access_log=False)
    )
    thread = threading.Thread(
        target=server.run, name="letools-annotations", daemon=True
    )
    thread.start()
    return server, thread


def serve_visualizer(target_text: str, config: VisualizerConfig) -> int:
    """Run the complete upstream application until it exits or is interrupted."""

    target = resolve_visualizer_target(target_text)
    installation = prepare_visualizer(
        cache_dir=config.cache_dir,
        bun=config.bun,
        force=config.force_setup,
        install_dependencies=True,
    )
    assert installation.bun is not None
    browser_host = _browser_host(config.host)
    environment = os.environ.copy()
    data_server: LocalDatasetServer | None = None
    data_thread: threading.Thread | None = None
    annotation_server: Any | None = None
    annotation_thread: threading.Thread | None = None

    try:
        data_origin: str | None = None
        if target.local_root is not None:
            data_server = LocalDatasetServer(
                (config.host, config.data_port),
                target.local_root,
                target.repo_id,
                doctor_max_episodes=config.doctor_max_episodes,
            )
            data_thread = threading.Thread(
                target=data_server.serve_forever,
                name="letools-local-dataset",
                daemon=True,
            )
            data_thread.start()
            data_origin = (
                config.public_data_url
                or f"http://{browser_host}:{data_server.server_port}"
            ).rstrip("/")
            environment["DATASET_URL"] = data_origin
            environment["NEXT_PUBLIC_LEROBOT_DOCTOR_URL"] = f"{data_origin}/doctor"

        annotation_port = _free_port(config.host, config.annotation_port)
        annotation_origin: str | None = None
        if config.annotations:
            mapping = (
                (target.repo_id, target.local_root)
                if target.local_root is not None
                else None
            )
            annotation_server, annotation_thread = _start_annotation_server(
                installation.source, config.host, annotation_port, mapping
            )
            annotation_origin = (
                config.public_annotation_url
                or f"http://{browser_host}:{annotation_port}"
            ).rstrip("/")
            environment["NEXT_PUBLIC_ANNOTATE_BACKEND_URL"] = annotation_origin
            _wait_for_http(f"http://{browser_host}:{annotation_port}/api/health")
        else:
            environment.pop("NEXT_PUBLIC_ANNOTATE_BACKEND_URL", None)

        web_origin = f"http://{browser_host}:{config.port}"
        url = f"{web_origin}{target.route}"
        launch = {
            "url": url,
            "target": asdict(target),
            "application": str(installation.source),
            "upstream_commit": installation.upstream_commit,
            "bun_version": installation.bun_version,
            "dataset_origin": data_origin,
            "annotation_origin": annotation_origin,
            "doctor_max_episodes": config.doctor_max_episodes,
        }
        print(json.dumps(launch, indent=2, default=str), flush=True)

        if config.production:
            subprocess.run(
                [installation.bun, "run", "build"],
                cwd=installation.source,
                env=environment,
                check=True,
            )
            command = [
                installation.bun,
                "run",
                "start",
                "--hostname",
                config.host,
                "--port",
                str(config.port),
            ]
        else:
            command = [
                installation.bun,
                "run",
                "dev",
                "--hostname",
                config.host,
                "--port",
                str(config.port),
            ]
        process = subprocess.Popen(command, cwd=installation.source, env=environment)
        if config.open_browser:
            _wait_for_http(web_origin)
            webbrowser.open(url)
        try:
            return process.wait()
        except KeyboardInterrupt:
            process.terminate()
            try:
                return process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                return process.wait()
    finally:
        if annotation_server is not None:
            annotation_server.should_exit = True
        if annotation_thread is not None:
            annotation_thread.join(timeout=5)
        if data_server is not None:
            data_server.shutdown()
            data_server.server_close()
        if data_thread is not None:
            data_thread.join(timeout=5)


__all__ = [
    "VisualizerConfig",
    "VisualizerInstallation",
    "VisualizerTarget",
    "prepare_visualizer",
    "resolve_visualizer_target",
    "serve_visualizer",
]
