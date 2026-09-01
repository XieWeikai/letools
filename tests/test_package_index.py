from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path


def test_package_index_preserves_documentation_home(tmp_path: Path) -> None:
    """The unified Pages artifact must keep docs at / and wheels at /simple."""

    wheels = tmp_path / "wheels"
    output = tmp_path / "site"
    wheels.mkdir()
    output.mkdir()
    home = "<!doctype html><title>LeTools documentation</title>"
    (output / "index.html").write_text(home, encoding="utf-8")
    wheel = wheels / "letools_native-0.2.0-py3-none-any.whl"
    wheel.write_bytes(b"wheel fixture")

    subprocess.run(
        [
            sys.executable,
            "scripts/build_simple_index.py",
            str(wheels),
            str(output),
            "XieWeikai/letools",
            "native-v0.2.0",
        ],
        check=True,
    )

    assert (output / "index.html").read_text() == home
    package_index = (output / "simple/letools-native/index.html").read_text()
    assert wheel.name in package_index
    assert f"#sha256={hashlib.sha256(wheel.read_bytes()).hexdigest()}" in package_index
