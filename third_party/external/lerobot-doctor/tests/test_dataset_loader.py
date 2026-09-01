"""Tests for dataset loading and format detection."""

from __future__ import annotations

import json
import zipfile

from lerobot_doctor.dataset_loader import load_dataset, load_local
from tests.conftest import create_dataset


def test_load_zip_archive_with_nested_root(tmp_path):
    root = create_dataset(tmp_path / "source")
    zip_path = tmp_path / "dataset.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for path in root.rglob("*"):
            if path.is_file():
                zf.write(path, "org/name/" + str(path.relative_to(root)))

    ds = load_dataset(str(zip_path), max_episodes=1)

    assert ds.archive_path == zip_path
    assert ds.archive_inner_root == "org/name"
    assert ds.display_path == str(zip_path)
    assert ds.info is not None
    assert ds.info.format_version == "v3"
    assert len(ds.episodes_data) == 1


def test_v2_jsonl_metadata_detection(tmp_path):
    root = create_dataset(tmp_path / "dataset", n_episodes=2)
    info_path = root / "meta" / "info.json"
    info = json.loads(info_path.read_text())
    info["codebase_version"] = "v2.1"
    info_path.write_text(json.dumps(info))

    # Convert v3-only metadata paths into v2-style JSONL metadata.
    import shutil

    shutil.rmtree(root / "meta" / "episodes")
    (root / "meta" / "episodes.jsonl").write_text(
        "\n".join(
            json.dumps({"episode_index": i, "length": 10, "tasks": ["pick and place"]})
            for i in range(2)
        )
        + "\n"
    )
    (root / "meta" / "tasks.parquet").unlink()
    (root / "meta" / "tasks.jsonl").write_text(
        json.dumps({"task_index": 0, "task": "pick and place"}) + "\n"
    )

    ds = load_local(root)

    assert ds.info is not None
    assert ds.info.format_version == "v2"
    assert len(ds.episodes_meta) == 2
    assert ds.tasks == [{"task_index": 0, "task": "pick and place"}]
