"""Semantic acceptance tests; execute under Slurm on cluster installations."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import av
import numpy as np
import pandas as pd
import pytest
from letools_editor import EditConfig, VideoEdit, edit_dataset, plan_edit
from letools_editor.cli import episode_selection, main
from test_roundtrip import make_v21

from letools import (
    ConversionConfig,
    compare_datasets,
    convert,
    open_dataset,
    validate_dataset,
)
from letools._stats import aggregate_episode_stats
from letools._video import packet_digests
from letools_editor.media import packet_remux_safe

KEY = "observation.images.front"


def _hashes(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


def decoded(path: Path) -> np.ndarray:
    with av.open(str(path)) as container:
        return np.stack(
            [frame.to_ndarray(format="rgb24") for frame in container.decode(video=0)]
        )


def make_dataset(root: Path, version: str, codec: str = "mjpeg") -> Path:
    """Three visually distinct episodes; enough to remove a middle interval."""

    v21 = make_v21(root / "v21")
    info_path = v21 / "meta/info.json"
    info = json.loads(info_path.read_text())
    info["features"]["observation.state"]["shape"] = [2]
    info["features"]["action"]["shape"] = [2]
    # Keep train/validation split names to catch accidental train-only output.
    info["splits"] = {"train": "0:2", "validation": "2:3"}
    info["video_path"] = (
        "videos/chunk-{episode_chunk:03d}/{video_key}/episode_{episode_index:06d}.mp4"
    )
    info["total_videos"] = 3
    info["features"][KEY] = {
        "dtype": "video",
        "shape": [24, 32, 3],
        "names": ["height", "width", "channels"],
        "info": {
            "video.fps": 30,
            "video.codec": codec,
            "video.pix_fmt": "yuvj420p" if codec == "mjpeg" else "yuv420p",
            "video.is_depth_map": False,
            "has_audio": False,
        },
    }
    info_path.write_text(json.dumps(info))
    stats_path = v21 / "meta/episodes_stats.jsonl"
    stats = [json.loads(line) for line in stats_path.read_text().splitlines()]
    for index, length in enumerate((3, 4, 2)):
        path = v21 / f"videos/chunk-000/{KEY}/episode_{index:06d}.mp4"
        path.parent.mkdir(parents=True, exist_ok=True)
        with av.open(str(path), "w") as output:
            stream = output.add_stream(codec, rate=30)
            stream.width, stream.height = 32, 24
            stream.pix_fmt = "yuvj420p" if codec == "mjpeg" else "yuv420p"
            stream.codec_context.thread_count = 1
            if codec == "libx264":
                stream.options = {"bf": "0", "crf": "0"}
            for frame_index in range(length):
                pixels = np.zeros((24, 32, 3), dtype=np.uint8)
                pixels[:, :, 0] = 20 + index * 60 + frame_index
                pixels[:, :, 1] = np.arange(32)
                pixels[:, :, 2] = np.arange(24)[:, None] * 5
                for packet in stream.encode(
                    av.VideoFrame.from_ndarray(pixels, format="rgb24")
                ):
                    output.mux(packet)
            for packet in stream.encode():
                output.mux(packet)
        stats[index]["stats"][KEY] = pixel_stats(decoded(path))
    stats_path.write_text("".join(json.dumps(row) + "\n" for row in stats))
    if version == "v2.1":
        return v21
    target = root / "v30"
    convert(v21, target, "v3.0", config=ConversionConfig(workers=2, video_workers=2))
    return target


def pixel_stats(frames: np.ndarray) -> dict:
    normalized = frames.astype(np.float64) / 255
    return {
        **{
            key: function(normalized, axis=(0, 1, 2)).reshape(3, 1, 1).tolist()
            for key, function in [
                ("min", np.min),
                ("max", np.max),
                ("mean", np.mean),
                ("std", np.std),
            ]
        },
        "count": [len(frames)],
    }


@pytest.mark.parametrize("version", ["v2.1", "v3.0"])
def test_fused_delete_task_feature_and_packet_preservation(tmp_path, version):
    root = make_dataset(tmp_path / "input", version)
    before = _hashes(root)
    target = tmp_path / "output"
    config = EditConfig(
        task_by_episode={2: "new task"},
        delete_episodes=frozenset({1}),
        remove_features=frozenset({"action"}),
        batch_rows=2,
        workers=2,
    )
    result = edit_dataset(root, target, config)
    assert _hashes(root) == before
    assert result.episode_index_map == {0: 0, 2: 1}
    assert validate_dataset(target, deep=True).valid
    source, output = open_dataset(root), open_dataset(target)
    assert output.metadata.splits == {"train": "0:1", "validation": "1:2"}
    assert output.metadata.tasks == {0: "test task", 1: "new task"}
    if version == "v3.0":
        assert pd.read_parquet(target / "meta/tasks.parquet").index.tolist() == [
            "test task",
            "new task",
        ]
    assert output.metadata.total_frames == 5
    start = 0
    for old, new in zip(
        (source.episodes[0], source.episodes[2]), output.episodes, strict=True
    ):
        expected = source.read_episode(old)
        actual = output.read_episode(new)
        assert "action" not in actual.column_names
        assert actual["observation.state"].equals(expected["observation.state"])
        assert actual["index"].to_pylist() == list(range(start, start + new.length))
        assert actual["episode_index"].to_pylist() == [new.index] * new.length
        assert actual["task_index"].to_pylist() == [new.index] * new.length
        assert new.stats["index"]["mean"] == [start + (new.length - 1) / 2]
        assert "action" not in new.stats
        assert packet_digests([old.videos[KEY]]) == packet_digests([new.videos[KEY]])
        start += new.length
    # Deleted frames must be physically absent, not retained as dead video.
    assert (
        sum(
            len(decoded(path))
            for path in {ep.videos[KEY].path for ep in output.episodes}
        )
        == 5
    )
    assert json.loads(
        (target / "meta/stats.json").read_text()
    ) == aggregate_episode_stats([ep.stats for ep in output.episodes])


@pytest.mark.parametrize("version", ["v2.1", "v3.0"])
def test_noop_and_task_only_reuse_videos(tmp_path, version):
    root = make_dataset(tmp_path / "input", version)
    target = tmp_path / "noop"
    result = edit_dataset(root, target)
    assert result.plan.data_files_rewrite == 0
    assert result.plan.videos_transcode == result.plan.videos_remux == 0
    assert compare_datasets(root, target, check_videos=True).equal
    changed = tmp_path / "task"
    edit_dataset(root, changed, EditConfig(task_by_episode={1: "= unicode 新任务"}))
    source, output = open_dataset(root), open_dataset(changed)
    assert output.episodes[1].tasks == ("= unicode 新任务",)
    for old, new in zip(source.episodes, output.episodes, strict=True):
        assert (
            hashlib.sha256(old.videos[KEY].path.read_bytes()).digest()
            == hashlib.sha256(new.videos[KEY].path.read_bytes()).digest()
        )
    # Copies/reflinks must never become shared mutable hard links.
    copied = output.episodes[0].videos[KEY].path
    copied.write_bytes(b"changed independently")
    assert source.episodes[0].videos[KEY].path.read_bytes() != b"changed independently"


@pytest.mark.parametrize("version", ["v2.1", "v3.0"])
@pytest.mark.parametrize("codec", ["libx264", "mjpeg"])
@pytest.mark.parametrize("channels_first", [False, True])
def test_resize_codec_output_statistics_and_roundtrip(
    tmp_path, version, codec, channels_first
):
    root = make_dataset(tmp_path / "input", version)
    if channels_first:
        info_path = root / "meta/info.json"
        info = json.loads(info_path.read_text())
        feature = info["features"][KEY]
        feature["shape"] = [3, 24, 32]
        feature["names"] = ["channels", "height", "width"]
        feature["info"].update({"video.height": 24, "video.width": 32})
        info_path.write_text(json.dumps(info))
    output = tmp_path / "output"
    result = edit_dataset(
        root, output, EditConfig(video=VideoEdit(codec=codec, size=(16, 16)), workers=2)
    )
    assert result.plan.videos_transcode > 0
    source = open_dataset(output)
    assert source.metadata.features[KEY]["shape"] == (
        [3, 16, 16] if channels_first else [16, 16, 3]
    )
    assert source.metadata.features[KEY]["info"]["video.height"] == 16
    assert source.metadata.features[KEY]["info"]["video.width"] == 16
    assert source.metadata.features[KEY]["info"]["video.codec"] == (
        "h264" if codec == "libx264" else "mjpeg"
    )
    assert validate_dataset(output, deep=True).valid
    arrays = {
        path: decoded(path) for path in {ep.videos[KEY].path for ep in source.episodes}
    }
    for episode in source.episodes:
        video = episode.videos[KEY]
        first = round(video.start * source.metadata.fps)
        frames = arrays[video.path][first : first + episode.length]
        expected = pixel_stats(frames)
        for key in expected:
            # FFmpeg bundled decoder and PyAV may differ by a tiny amount in
            # RGB conversion. Compare within one RGB quantization step.
            np.testing.assert_allclose(
                episode.stats[KEY][key], expected[key], atol=1 / 255, rtol=1e-5
            )
    opposite = "v2.1" if version == "v3.0" else "v3.0"
    converted = tmp_path / "converted"
    convert(
        output, converted, opposite, config=ConversionConfig(workers=2, video_workers=2)
    )
    assert compare_datasets(output, converted, check_videos=True).equal
    # Full decoding catches unsafe inter-frame cuts that packet hashes miss.
    for path in {ep.videos[KEY].path for ep in open_dataset(converted).episodes}:
        assert len(decoded(path)) > 0


def test_delete_idr_aligned_h264_remuxes_safely(tmp_path):
    root = make_dataset(tmp_path / "input", "v3.0", "libx264")
    target = tmp_path / "output"
    config = EditConfig(delete_episodes=frozenset({1}))
    plan = plan_edit(root, target, config)
    # The fixture's episode boundaries are IDR-aligned and contain one frame
    # per packet, so the planner can preserve compressed bytes exactly.
    assert plan.videos_remux == 1
    assert plan.videos_transcode == 0
    assert not plan.warnings
    edit_dataset(root, target, config)
    source = open_dataset(target)
    frames = decoded(source.episodes[0].videos[KEY].path)
    assert len(frames) == 5
    assert frames[:3, :, :, 0].mean() < frames[3:, :, :, 0].mean() - 60


def test_h264_proof_fails_closed_for_non_idr_boundary(tmp_path):
    root = make_dataset(tmp_path / "input", "v3.0", "libx264")
    source = open_dataset(root)
    path = source.episodes[0].videos[KEY].path
    # Frame 1 is deliberately not an IDR boundary in this fixture. A future
    # encoder/container change must therefore retain the decode-and-encode
    # fallback instead of silently emitting undecodable inter frames.
    assert not packet_remux_safe(path, ((1, 3),))


@pytest.mark.parametrize(
    "workers, codec_threads, expected_threads",
    [(None, None, 2), (None, 1, 1), (None, 4, 4), (4, None, 1), (4, 3, 3)],
)
def test_concurrency_options_resolve_once(
    tmp_path, monkeypatch, workers, codec_threads, expected_threads
):
    """Omitted knobs permit tuning; each explicit knob survives to execution."""
    from types import SimpleNamespace
    from letools_editor import engine

    monkeypatch.setattr(engine, "inspect_resources", lambda: SimpleNamespace(
        effective_cpus=16, effective_memory_bytes=64 * 1024**3
    ))
    root = make_dataset(tmp_path / "source", "v2.1")
    config = EditConfig(
        workers=workers, codec_threads=codec_threads, video=VideoEdit()
    )
    manifest = engine._manifest(root, tmp_path / "output", config)
    assert manifest.plan.codec_threads == expected_threads
    assert manifest.config.codec_threads == expected_threads
    assert manifest.config.workers == manifest.plan.workers
    if workers is None:
        assert not manifest.plan.warnings


def test_cli_distinguishes_omitted_and_explicit_codec_threads():
    from letools_editor.cli import build_parser

    parser = build_parser()
    command = ["plan", "source", "destination", "--video-codec", "libx264"]
    assert parser.parse_args(command).codec_threads is None
    assert parser.parse_args([*command, "--codec-threads", "1"]).codec_threads == 1


@pytest.mark.parametrize("version", ["v2.1", "v3.0"])
def test_drop_camera_without_decoding(tmp_path, version, monkeypatch):
    root = make_dataset(tmp_path / "input", version)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Removed camera must not be opened")

    monkeypatch.setattr("letools_editor.engine.probe", forbidden)
    target = tmp_path / "output"
    edit_dataset(root, target, EditConfig(remove_features=frozenset({KEY})))
    assert not (target / "videos").exists()
    assert not open_dataset(target).metadata.video_keys
    assert validate_dataset(target, deep=True).valid


def test_readonly_plan_errors_and_rollback(tmp_path, monkeypatch):
    root = make_dataset(tmp_path / "input", "v3.0")
    target = tmp_path / "parent" / "output"
    plan_edit(root, target, EditConfig(task_by_episode={1: "task"}))
    assert not target.parent.exists()
    for destination in (root, root / "child", root.parent):
        with pytest.raises(ValueError, match="contain"):
            plan_edit(root, destination)
    for config in (
        EditConfig(delete_episodes=frozenset({0, 1, 2})),
        EditConfig(task_by_episode={99: "task"}),
        EditConfig(remove_features=frozenset({"unknown"})),
    ):
        with pytest.raises(ValueError):
            plan_edit(root, target, config)
    with pytest.raises(ValueError, match="system"):
        EditConfig(remove_features=frozenset({"index"}))
    edit_dataset(root, target)
    before = _hashes(target)

    def fail(*_args, **_kwargs):
        raise RuntimeError("injected encoder failure")

    monkeypatch.setattr("letools_editor.engine.execute_media", fail)
    with pytest.raises(RuntimeError, match="injected"):
        edit_dataset(root, target, EditConfig(video=VideoEdit(), overwrite=True))
    assert _hashes(target) == before
    assert not list(target.parent.glob(".*letools-editor*"))


def test_cli_and_core_dispatch(tmp_path, capsys):
    from letools.cli import main as core_main

    root = make_dataset(tmp_path / "input", "v2.1")
    assert main(["inspect", str(root), "--limit", "1"]) == 0
    assert len(json.loads(capsys.readouterr().out)["episode_preview"]) == 1
    assert episode_selection("0,2:4") == frozenset({0, 2, 3})
    assert (
        core_main(
            [
                "editor",
                "plan",
                str(root),
                str(tmp_path / "output"),
                "--set-task",
                "1=new task",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["data_files_rewrite"] == 1
    assert (
        main(["apply", str(root), str(tmp_path / "output"), "--set-task", "1=new task"])
        == 0
    )
    assert main(["plan", str(root), str(tmp_path / "bad"), "--set-task", "bad"]) == 2
