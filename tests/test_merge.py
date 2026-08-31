from __future__ import annotations

import hashlib
import json
from pathlib import Path

import h5py
import pytest

from letools import (
    ConversionConfig,
    HDF5Source,
    compare_datasets,
    convert,
    merge_datasets,
    open_dataset,
    plan_merge,
    validate_dataset,
)
from letools.cli import main
from test_hdf5 import make_hdf5


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_pair(tmp_path: Path, version: str) -> tuple[Path, Path]:
    first_root, mapping = make_hdf5(tmp_path / "first-hdf5")
    second_root, _ = make_hdf5(tmp_path / "second-hdf5")
    # Reverse first appearance so both sources use different local task indices.
    with h5py.File(second_root / "episode_0.hdf5", "r+") as handle:
        handle["language_instruction"][()] = "place cloth"
    with h5py.File(second_root / "episode_1.hdf5", "r+") as handle:
        handle["language_instruction"][()] = "fold cloth"
    first = tmp_path / f"first-{version}"
    second = tmp_path / f"second-{version}"
    config = ConversionConfig(workers=2, video_workers=2)
    convert(HDF5Source(first_root, mapping), first, version, config=config)
    convert(HDF5Source(second_root, mapping), second, version, config=config)
    return first, second


@pytest.mark.parametrize("version", ["v2.1", "v3.0"])
def test_merge_remaps_tasks_and_preserves_video_bytes(tmp_path: Path, version: str) -> None:
    first, second = _make_pair(tmp_path, version)
    destination = tmp_path / "merged"
    result = merge_datasets(
        [first, second], destination, data_workers=2, file_workers=2
    )
    assert result.tasks == 2
    assert result.cloned_files + result.copied_files > 0
    assert validate_dataset(destination, deep=True).valid

    inputs = (open_dataset(first), open_dataset(second))
    output = open_dataset(destination)
    assert output.metadata.tasks == {0: "fold cloth", 1: "place cloth"}
    assert [episode.tasks for episode in output.episodes] == [
        episode.tasks for source in inputs for episode in source.episodes
    ]
    assert output.read_episode(output.episodes[2])["task_index"].to_pylist() == [1] * 4
    assert output.read_episode(output.episodes[3])["task_index"].to_pylist() == [0] * 3

    expected_videos = [
        _sha256(source.media_input(episode, "observation.images.front").path)
        for source in inputs
        for episode in source.episodes
    ]
    actual_videos = [
        _sha256(output.media_input(episode, "observation.images.front").path)
        for episode in output.episodes
    ]
    if version == "v2.1":
        assert actual_videos == expected_videos
    else:
        assert set(actual_videos) == set(expected_videos)
    # Packet-level comparison covers episode slices even when v3 episodes share files.
    assert compare_datasets(
        output,
        output,
        check_videos=True,
    ).equal


def test_merge_rejects_incompatibility_before_publication(tmp_path: Path) -> None:
    first, second = _make_pair(tmp_path, "v2.1")
    with pytest.raises(ValueError, match="must not contain"):
        merge_datasets([first, second], first / "output")

    info_path = second / "meta/info.json"
    info = json.loads(info_path.read_text(encoding="utf-8"))
    info["fps"] = 20
    info_path.write_text(json.dumps(info), encoding="utf-8")
    destination = tmp_path / "existing"
    destination.mkdir()
    sentinel = destination / "sentinel"
    sentinel.write_text("keep", encoding="utf-8")

    with pytest.raises(ValueError, match="FPS differs"):
        merge_datasets([first, second], destination, overwrite=True)
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_merge_plan_cache_and_cli(tmp_path: Path, monkeypatch, capsys) -> None:
    first, second = _make_pair(tmp_path, "v2.1")
    cache = tmp_path / "plan.json"
    monkeypatch.setattr("letools.merge._cache_path", lambda _fingerprint: cache)
    destination = tmp_path / "merged"
    plan = plan_merge(
        [first, second],
        destination,
        calibrate=True,
        calibration_seconds=2,
        calibration_bytes=64 * 1024**2,
    )
    assert plan.confidence == "calibrated"
    assert cache.is_file()
    cached = plan_merge([first, second], destination)
    assert cached.cache_hit

    assert (
        main(
            [
                "merge",
                str(first),
                str(second),
                "--output",
                str(destination),
                "--data-workers",
                "2",
                "--file-workers",
                "2",
            ]
        )
        == 0
    )
    assert '"version": "v2.1"' in capsys.readouterr().out
    assert validate_dataset(destination, deep=True).valid
