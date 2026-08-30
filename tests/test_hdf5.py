from __future__ import annotations

from pathlib import Path

import av
import h5py
import numpy as np
import letools.plugins.hdf5 as hdf5_plugin

from letools import (
    ConversionConfig,
    HDF5Mapping,
    HDF5NumericField,
    HDF5Source,
    HDF5VideoField,
    compare_datasets,
    convert,
    open_dataset,
    plan_conversion,
    validate_dataset,
)
from test_video import _make_jpegs


def make_hdf5(root: Path) -> tuple[Path, HDF5Mapping]:
    """Create two deterministic one-file-per-episode HDF5 fixtures."""

    root.mkdir(parents=True)
    for episode_index, length in enumerate((4, 3)):
        path = root / f"episode_{episode_index}.hdf5"
        state = (
            np.arange(length * 2, dtype=np.float32).reshape(length, 2)
            + episode_index * 10
        )
        frames = _make_jpegs(20 + episode_index * 20, count=length)
        with h5py.File(path, "w") as handle:
            handle.create_dataset("observations/qpos", data=state)
            handle.create_dataset("action", data=state + np.float32(0.5))
            video = handle.create_dataset(
                "observations/images/front",
                shape=(length,),
                dtype=h5py.vlen_dtype(np.dtype("uint8")),
            )
            for index, frame in enumerate(frames):
                video[index] = np.frombuffer(frame, dtype=np.uint8)
            handle.create_dataset(
                "language_instruction",
                data="fold cloth" if episode_index == 0 else "place cloth",
                dtype=h5py.string_dtype("utf-8"),
            )
    mapping = HDF5Mapping(
        fps=10,
        numeric_fields=(
            HDF5NumericField("observations/qpos", "observation.state"),
            HDF5NumericField("action", "action"),
        ),
        video_fields=(
            HDF5VideoField(
                "observations/images/front",
                "observation.images.front",
                width=32,
                height=24,
            ),
        ),
        task_key="language_instruction",
        robot_type="test-arm",
    )
    return root, mapping


def _decoded_frames(dataset_path: Path) -> list[int]:
    source = open_dataset(dataset_path)
    counts = []
    for episode in source.episodes:
        media = episode.videos["observation.images.front"]
        with av.open(str(media.path)) as container:
            counts.append(len(list(container.decode(video=0))))
    return counts


def test_hdf5_source_converts_to_both_versions(tmp_path: Path) -> None:
    root, mapping = make_hdf5(tmp_path / "hdf5")
    source = HDF5Source(root, mapping)
    assert source.metadata.total_episodes == 2
    assert source.metadata.total_frames == 7
    assert source.metadata.tasks == {0: "fold cloth", 1: "place cloth"}
    assert source.read_episode(source.episodes[1])["index"].to_pylist() == [4, 5, 6]
    assert np.allclose(
        source.read_episode(source.episodes[0])["timestamp"].to_numpy(),
        [0.0, 0.1, 0.2, 0.3],
    )

    plan = plan_conversion(source, tmp_path / "planned-v30", "v3.0", use_cache=False)
    assert plan.dataset.source_kind == "letools.plugins.hdf5.HDF5Source"
    assert plan.dataset.encoding_media_inputs == 2
    assert plan.dataset.source_configuration

    config = ConversionConfig(workers=2, video_workers=2)
    v21 = tmp_path / "v21"
    v30 = tmp_path / "v30"
    convert(source, v21, "v2.1", config=config)
    convert(source, v30, "v3.0", config=config)

    assert validate_dataset(v21, deep=True).valid
    assert validate_dataset(v30, deep=True).valid
    assert compare_datasets(v21, v30).equal
    assert _decoded_frames(v21) == [4, 3]
    # Both v3 episodes share a shard, so each metadata slice references all
    # decoded shard frames even though its timestamp range remains per episode.
    assert _decoded_frames(v30) == [7, 7]


def test_hdf5_mapping_rejects_implicit_or_ambiguous_semantics(tmp_path: Path) -> None:
    root, mapping = make_hdf5(tmp_path / "hdf5")
    invalid = HDF5Mapping(
        fps=10,
        numeric_fields=mapping.numeric_fields,
        task_key="language_instruction",
        default_task="ambiguous",
    )
    try:
        HDF5Source(root, invalid)
    except ValueError as error:
        assert "exactly one" in str(error)
    else:
        raise AssertionError("Ambiguous task mapping should be rejected")


def test_hdf5_frame_sequence_reuses_one_file_handle(tmp_path: Path, monkeypatch) -> None:
    root, mapping = make_hdf5(tmp_path / "hdf5")
    source = HDF5Source(root, mapping)
    sequence = source.media_input(source.episodes[0], "observation.images.front")
    original_file = h5py.File
    open_count = 0

    def counting_file(*args, **kwargs):
        nonlocal open_count
        open_count += 1
        return original_file(*args, **kwargs)

    monkeypatch.setattr(hdf5_plugin.h5py, "File", counting_file)
    batches = list(sequence.iter_batches(2))

    assert [len(batch) for batch in batches] == [2, 2]
    assert open_count == 1
