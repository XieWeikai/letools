from __future__ import annotations

import json
import threading
from pathlib import Path
from urllib.request import Request, urlopen

from fastapi.testclient import TestClient

from letools import ConversionConfig, convert
from letools.cli import main
from letools.external import visualizer_source
from letools.visualizer import (
    _load_annotation_backend,
    prepare_visualizer,
    resolve_visualizer_target,
)
from letools.visualizer_server import LocalDatasetServer
from test_roundtrip import make_v21


def _fake_bun(path: Path) -> Path:
    path.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"--version\" ]; then echo 1.3.9; exit 0; fi\n"
        "if [ \"$1\" = \"install\" ]; then mkdir -p node_modules; exit 0; fi\n"
        "exit 2\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def test_prepare_visualizer_is_patched_cached_copy(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    installation = prepare_visualizer(
        cache_dir=cache, force=True, install_dependencies=False
    )
    relative = Path("src/app/[org]/[dataset]/[episode]/episode-viewer.tsx")
    pristine = (visualizer_source() / relative).read_text(encoding="utf-8")
    prepared = (installation.source / relative).read_text(encoding="utf-8")
    assert "NEXT_PUBLIC_LEROBOT_DOCTOR_URL" not in pristine
    assert "NEXT_PUBLIC_LEROBOT_DOCTOR_URL" in prepared
    assert installation.upstream_commit.startswith("dc59887")
    assert not list(installation.source.glob(".letools-*.json"))
    assert (cache / ".letools-source.json").is_file()

    cached = prepare_visualizer(cache_dir=cache, install_dependencies=False)
    assert cached.source == installation.source
    assert cached.fingerprint == installation.fingerprint


def test_visualizer_setup_uses_locked_bun_install(tmp_path: Path, capsys) -> None:
    bun = _fake_bun(tmp_path / "bun")
    assert (
        main(
            [
                "visualizer",
                "setup",
                "--cache-dir",
                str(tmp_path / "cache"),
                "--bun",
                str(bun),
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["bun_version"] == "1.3.9"
    assert result["dependencies_installed"] is True


def test_visualizer_rebuilds_when_cached_source_is_missing(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    prepared = prepare_visualizer(cache_dir=cache, install_dependencies=False)
    prepared.source.rename(tmp_path / "removed-source")

    rebuilt = prepare_visualizer(cache_dir=cache, install_dependencies=False)
    assert (rebuilt.source / "package.json").is_file()


def test_resolve_visualizer_target(tmp_path: Path) -> None:
    local = make_v21(tmp_path / "local data")
    target = resolve_visualizer_target(str(local))
    assert target.is_local
    assert target.repo_id.startswith("local/local-data-")
    assert target.route.endswith("/0")

    hub = resolve_visualizer_target("lerobot/pusht")
    assert not hub.is_local
    assert hub.route == "/lerobot/pusht/0"


def test_local_dataset_server_range_and_doctor(tmp_path: Path) -> None:
    root = make_v21(tmp_path / "dataset")
    server = LocalDatasetServer(
        ("127.0.0.1", 0),
        root,
        "local/test",
        doctor_max_episodes=2,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    try:
        info_url = f"{origin}/local/test/resolve/main/meta/info.json"
        with urlopen(info_url) as response:
            assert json.load(response)["codebase_version"] == "v2.1"

        request = Request(info_url, headers={"Range": "bytes=0-15"})
        with urlopen(request) as response:
            assert response.status == 206
            assert response.headers["Content-Range"].startswith("bytes 0-15/")
            assert len(response.read()) == 16

        with urlopen(f"{origin}/doctor/report.json") as response:
            report = json.load(response)
        assert report["dataset_path"] == str(root)
        assert len(report["checks"]) == 12
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_annotation_backend_maps_local_alias(tmp_path: Path) -> None:
    v21 = make_v21(tmp_path / "v21")
    v30 = tmp_path / "v30"
    convert(v21, v30, "v3.0", config=ConversionConfig(workers=1))
    target = resolve_visualizer_target(str(v30))
    installation = prepare_visualizer(
        cache_dir=tmp_path / "cache", install_dependencies=False
    )
    app = _load_annotation_backend(
        installation.source, (target.repo_id, v30.resolve())
    )
    response = TestClient(app).post(
        "/api/dataset/load", json={"repo_id": target.repo_id}
    )
    assert response.status_code == 200
    assert response.json()["local_path"] == str(v30.resolve())
