"""Build a hash-pinned PEP 503 page for released letools-native wheels."""

from __future__ import annotations

import hashlib
import html
import sys
from pathlib import Path


def main() -> None:
    """Generate package and root index pages from one release wheel directory."""

    wheel_dir, output_dir, repository, tag = map(Path, sys.argv[1:5])
    repository_name = repository.as_posix()
    tag_name = tag.as_posix()
    wheels = sorted(wheel_dir.glob("letools_native-*.whl"))
    if not wheels:
        raise SystemExit("no letools-native wheels found")

    package_dir = output_dir / "simple/letools-native"
    package_dir.mkdir(parents=True, exist_ok=True)
    links = []
    for wheel in wheels:
        digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
        url = (
            f"https://github.com/{repository_name}/releases/download/"
            f"{tag_name}/{wheel.name}#sha256={digest}"
        )
        links.append(f'<a href="{html.escape(url)}">{html.escape(wheel.name)}</a>')
    page = "<!doctype html>\n<html><body>\n" + "<br>\n".join(links) + "\n</body></html>\n"
    (package_dir / "index.html").write_text(page, encoding="utf-8")
    (output_dir / "index.html").write_text(
        '<!doctype html>\n<a href="simple/letools-native/">letools-native</a>\n',
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
