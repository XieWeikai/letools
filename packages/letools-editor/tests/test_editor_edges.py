"""Cross-feature, resource-budget, and failure-atomicity regression cases."""

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from letools_editor import EditConfig, VideoEdit, edit_dataset, plan_edit
from test_editor import KEY, _hashes, make_dataset

from letools import ConversionConfig, convert, open_dataset, validate_dataset


@pytest.mark.parametrize("version", ["v2.1", "v3.0"])
def test_selected_camera_and_multitask_rows(tmp_path, version):
    """Selection leaves other cameras and frame-level task labels intact."""
    root = make_dataset(tmp_path / "input", "v2.1")
    second = "observation.images.wrist"
    info_path = root / "meta/info.json"
    info = json.loads(info_path.read_text())
    info["features"][second] = json.loads(json.dumps(info["features"][KEY]))
    info["total_videos"] = 6
    info["total_tasks"] = 2
    info_path.write_text(json.dumps(info))
    (root / "meta/tasks.jsonl").write_text(
        '{"task_index": 0, "task": "test task"}\n{"task_index": 1, "task": "second task"}\n'
    )
    episodes_path = root / "meta/episodes.jsonl"
    rows = [json.loads(line) for line in episodes_path.read_text().splitlines()]
    rows[1]["tasks"] = ["test task", "second task"]
    episodes_path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    data_path = root / "data/chunk-000/episode_000001.parquet"
    table = pq.read_table(data_path)
    values = [0, 1, 0, 1]
    table = table.set_column(
        table.schema.get_field_index("task_index"), "task_index", pa.array(values)
    )
    pq.write_table(table, data_path)
    stats_path = root / "meta/episodes_stats.jsonl"
    stats = [json.loads(line) for line in stats_path.read_text().splitlines()]
    for row in stats:
        row["stats"][second] = row["stats"][KEY]
    stats[1]["stats"]["task_index"] = {
        "min": [0],
        "max": [1],
        "mean": [0.5],
        "std": [0.5],
        "count": [4],
    }
    stats_path.write_text("".join(json.dumps(row) + "\n" for row in stats))
    for path in (root / "videos/chunk-000" / KEY).glob("*.mp4"):
        destination = root / "videos/chunk-000" / second / path.name
        destination.parent.mkdir(exist_ok=True)
        shutil.copyfile(path, destination)
    if version == "v3.0":
        v30 = tmp_path / "v30"
        convert(
            root,
            v30,
            version,
            config=ConversionConfig(
                workers=2,
                video_workers=2,
                data_file_size_mb=0.001,
                video_file_size_mb=0.001,
            ),
        )
        root = v30
    target = tmp_path / "output"
    edit_dataset(
        root,
        target,
        EditConfig(
            task_by_episode={0: "new"}, video=VideoEdit(size=(16, 16), keys=(KEY,))
        ),
    )
    source, output = open_dataset(root), open_dataset(target)
    assert output.read_episode(output.episodes[1])["task_index"].to_pylist() == values
    assert output.episodes[1].tasks == ("test task", "second task")
    assert output.metadata.features[second]["shape"] == [24, 32, 3]
    for old, new in zip(source.episodes, output.episodes, strict=True):
        assert (
            old.videos[second].path.read_bytes() == new.videos[second].path.read_bytes()
        )
    assert validate_dataset(target, deep=True).valid


def test_publish_failure_restores_existing_output(tmp_path, monkeypatch):
    root = make_dataset(tmp_path / "input", "v2.1")
    target = tmp_path / "output"
    edit_dataset(root, target)
    before = _hashes(target)
    original_rename = Path.rename

    def fail_publication(path, destination):
        if path.name.startswith(".output.letools-editor-"):
            raise OSError("injected publication failure")
        return original_rename(path, destination)

    monkeypatch.setattr(Path, "rename", fail_publication)
    with pytest.raises(OSError, match="publication failure"):
        edit_dataset(
            root, target, EditConfig(task_by_episode={0: "new"}, overwrite=True)
        )
    assert _hashes(target) == before
    assert not list(target.parent.glob(".output*"))


def test_resource_caps_and_bad_pixel_format_rollback(tmp_path, monkeypatch):
    root = make_dataset(tmp_path / "input", "v2.1")
    target = tmp_path / "output"
    monkeypatch.setattr(
        "letools_editor.engine.inspect_resources",
        lambda: SimpleNamespace(effective_cpus=4, effective_memory_bytes=512 * 1024**2),
    )
    plan = plan_edit(
        root, target, EditConfig(video=VideoEdit(size=(16, 16)), workers=64)
    )
    assert plan.workers <= 2
    # Upscaling increases encoder/frame working sets even for a tiny source.
    enlarged = plan_edit(
        root, target, EditConfig(video=VideoEdit(size=(4096, 4096)), workers=64)
    )
    assert enlarged.workers == 1
    with pytest.raises(RuntimeError, match="FFmpeg failed"):
        edit_dataset(
            root, target, EditConfig(video=VideoEdit(pixel_format="not_a_pixel_format"))
        )
    assert not target.exists()
    assert not list(target.parent.glob(".output*"))
