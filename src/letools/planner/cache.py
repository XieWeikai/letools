from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any


_CACHE_SCHEMA = 1


def default_cache_directory() -> Path:
    root = os.environ.get("XDG_CACHE_HOME")
    return Path(root) / "letools" / "planner-v1" if root else Path.home() / ".cache/letools/planner-v1"


def load_cached_choice(
    fingerprint: str,
    cache_directory: Path | None = None,
) -> dict[str, Any] | None:
    path = (cache_directory or default_cache_directory()) / f"{fingerprint}.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if value.get("schema_version") != _CACHE_SCHEMA:
        return None
    if not isinstance(value.get("expires_at"), (int, float)) or value["expires_at"] <= time.time():
        return None
    choice = value.get("choice")
    return choice if isinstance(choice, dict) else None


def save_cached_choice(
    fingerprint: str,
    choice: dict[str, Any],
    *,
    ttl_seconds: float,
    cache_directory: Path | None = None,
) -> None:
    directory = cache_directory or default_cache_directory()
    directory.mkdir(parents=True, exist_ok=True)
    value = {
        "schema_version": _CACHE_SCHEMA,
        "created_at": time.time(),
        "expires_at": time.time() + ttl_seconds,
        "choice": choice,
    }
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            prefix=f".{fingerprint}-",
            suffix=".tmp",
            dir=directory,
            delete=False,
        ) as handle:
            json.dump(value, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            temporary = Path(handle.name)
        temporary.replace(directory / f"{fingerprint}.json")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
