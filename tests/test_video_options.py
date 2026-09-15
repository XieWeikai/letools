"""User-visible encoding policy across CLI, planning, writers, and workers.

Run these conversion tests under the repository's Slurm test allocation.
Lossy encodings are checked after decoding, not by packet equality.
"""

from dataclasses import asdict, replace
import json
from pathlib import Path
import time

import av
import numpy as np
import pytest

from letools import (
    ConversionConfig,
    VideoEncodingConfig,
    convert,
    open_dataset,
    validate_dataset,
)
from letools.cli import main
from letools.distributed import (
    WorkerConfig,
    hdf5_source_spec,
    plan_distributed_conversion,
    run_distributed_task,
)
from letools.distributed.types import DistributedPlan
from letools.planner import plan_conversion, PerformanceOverrides
from letools.planner.calibrate import _FrameSample, _v21_video_jobs, _v30_video_jobs
from letools.plugins import HDF5Source
from letools.tools.hdf5_preset import HDF5Preset
from test_hdf5 import make_hdf5
from test_roundtrip import make_v21
from test_video import BytesFrameSequence, _make_jpegs


ENCODING = VideoEncodingConfig(
    codec="mpeg4", pixel_format="yuv420p", batch_frames=2, codec_threads=2
)
KEY = "observation.images.front"


def check_output(path: Path, codec="mpeg4", pixel_format="yuv420p"):
    assert validate_dataset(path, deep=True).valid
    source = open_dataset(path)
    assert source.metadata.total_frames == 7
    for episode, expected in zip(source.episodes, (20, 40), strict=True):
        media = episode.videos[KEY]
        with av.open(str(media.path)) as container:
            stream = container.streams.video[0]
            assert stream.codec_context.name == codec
            assert stream.pix_fmt == pixel_format
            frames = [
                frame
                for frame in container.decode(video=0)
                if media.start - 1e-6 <= float(frame.time) < media.end - 1e-6
            ]
        assert len(frames) == episode.length
        for index, frame in enumerate(frames):
            assert (
                abs(float(frame.to_ndarray(format="rgb24").mean()) - (expected + index))
                < 8
            )
        feature = source.metadata.info["features"][KEY]
        assert feature["info"]["video.codec"] == codec
        assert feature["info"]["video.pix_fmt"] == pixel_format


@pytest.mark.parametrize("target", ["v2.1", "v3.0"])
@pytest.mark.parametrize("auto", [False, True])
def test_cli_encoding_options_reach_actual_media(tmp_path, target, auto, capsys):
    root, mapping = make_hdf5(tmp_path / "input")
    preset = tmp_path / "preset.json"
    preset.write_text(json.dumps(HDF5Preset(name="test", mapping=mapping).to_dict()))
    output = tmp_path / "output"
    argv = [
        "convert",
        str(root),
        str(output),
        "--source-format",
        "hdf5",
        "--preset",
        str(preset),
        "--to",
        target,
        "--workers",
        "1",
        "--video-workers",
        "1",
        "--video-codec",
        "mpeg4",
        "--video-pixel-format",
        "yuv420p",
        "--video-batch-frames",
        "2",
        "--video-codec-threads",
        "2",
    ]
    if auto:
        argv += ["--auto", "--no-cache"]
    assert main(argv) == 0
    result = json.loads(capsys.readouterr().out)
    if auto:
        assert result["plan"]["video_encoding"] == asdict(ENCODING)
    check_output(output)


@pytest.mark.parametrize("target", ["v2.1", "v3.0"])
def test_distributed_encoding_and_legacy_manifest(tmp_path, target):
    root, mapping = make_hdf5(tmp_path / "input")
    output, job = tmp_path / "output", tmp_path / "job"
    plan = plan_distributed_conversion(
        hdf5_source_spec(root, mapping),
        output,
        target,
        job,
        task_count=2,
        worker=WorkerConfig(1, 1, video_encoding=ENCODING),
    )
    recovered = DistributedPlan.from_dict(json.loads(json.dumps(plan.to_dict())))
    assert recovered.worker.video_encoding == ENCODING
    assert recovered.worker.required_cpus == 2
    legacy = plan.to_dict()
    legacy["worker"].pop("video_encoding")
    legacy["worker"].pop("video_cpu_per_worker")
    assert DistributedPlan.from_dict(legacy).worker.video_encoding is None
    run_distributed_task(job, 0)
    run_distributed_task(job, 1)
    check_output(output)


def test_plan_cache_and_resource_constraints_use_encoding(tmp_path, monkeypatch):
    root, mapping = make_hdf5(tmp_path / "input")
    source = HDF5Source(root, mapping)
    original = plan_conversion(source, tmp_path / "out", "v3.0", use_cache=False)
    import letools.planner.api as api

    monkeypatch.setattr(
        api, "inspect_resources", lambda: replace(original.resources, effective_cpus=8)
    )
    plan = plan_conversion(
        source, tmp_path / "out", "v3.0", video_encoding=ENCODING, use_cache=False
    )
    assert plan.fingerprint != original.fingerprint
    assert plan.video_workers * ENCODING.codec_threads <= 8
    assert plan.conversion_config().video_encoding == ENCODING
    with pytest.raises(ValueError, match="CPU or memory"):
        plan_conversion(
            source,
            tmp_path / "out",
            "v3.0",
            video_encoding=ENCODING,
            overrides=PerformanceOverrides(video_workers=5),
        )
    for field, value in [
        ("batch_frames", 3),
        ("codec_threads", 1),
        ("pixel_format", None),
    ]:
        alternate = plan_conversion(
            source,
            tmp_path / "out",
            "v3.0",
            video_encoding=replace(ENCODING, **{field: value}),
            use_cache=False,
        )
        assert alternate.fingerprint != plan.fingerprint
    from letools.planner.cache import save_cached_choice

    cache = tmp_path / "cache"
    save_cached_choice(
        plan.fingerprint,
        {
            "workers": 1,
            "video_workers": 1,
            "data_file_size_mb": plan.data_file_size_mb,
            "video_file_size_mb": plan.video_file_size_mb,
            "measurements": [
                {
                    "stage": "video",
                    "workers": 1,
                    "tasks": 1,
                    "input_bytes": 10,
                    "elapsed_seconds": 0.1,
                }
            ],
        },
        ttl_seconds=60,
        cache_directory=cache,
    )
    cached = plan_conversion(
        source, tmp_path / "out", "v3.0", video_encoding=ENCODING, cache_directory=cache
    )
    assert cached.cache_hit and cached.conversion_config().video_encoding == ENCODING
    changed = plan_conversion(
        source,
        tmp_path / "out",
        "v3.0",
        video_encoding=replace(ENCODING, batch_frames=3),
        cache_directory=cache,
    )
    assert not changed.cache_hit
    mux = plan_conversion(
        source,
        tmp_path / "out",
        "v3.0",
        video_encoding=VideoEncodingConfig(codec_threads=64),
        overrides=PerformanceOverrides(video_workers=8),
        use_cache=False,
    )
    assert mux.video_workers == 8  # Direct mux has no running encoder.


@pytest.mark.parametrize("command", [["plan"], ["dist", "plan"]])
def test_cli_plans_serialize_video_options(tmp_path, command, capsys):
    root, mapping = make_hdf5(tmp_path / "input")
    preset = tmp_path / "preset.json"
    preset.write_text(json.dumps(HDF5Preset(name="test", mapping=mapping).to_dict()))
    args = command + [
        str(root),
        str(tmp_path / "output"),
        "--to",
        "v3.0",
        "--preset",
        str(preset),
        "--video-codec",
        "mpeg4",
        "--video-pixel-format",
        "yuv420p",
        "--video-batch-frames",
        "2",
        "--video-codec-threads",
        "2",
    ]
    if command == ["dist", "plan"]:
        args += ["--job-dir", str(tmp_path / "job")]
    assert main(args) == 0
    result = json.loads(capsys.readouterr().out)
    config = result["worker"] if command == ["dist", "plan"] else result
    assert config["video_encoding"] == asdict(ENCODING)
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize("factory", [_v21_video_jobs, _v30_video_jobs])
def test_calibration_uses_requested_codec(tmp_path, factory):
    from letools.planner.calibrate import _run_jobs

    root, mapping = make_hdf5(tmp_path / "input")
    jobs = factory(HDF5Source(root, mapping), encoding=ENCODING)
    output = tmp_path / "probe"
    _run_jobs(jobs, 2, output)
    assert len(list(output.rglob("*.mp4"))) == 2
    for path in output.rglob("*.mp4"):
        with av.open(str(path)) as container:
            assert container.streams.video[0].codec_context.name == "mpeg4"
            assert len(list(container.decode(video=0))) > 0


def test_calibration_prefix_and_deadline():
    media = BytesFrameSequence(_make_jpegs(20, count=60), 32, 24)
    sample = _FrameSample(media, float("inf"), 48)
    assert sum(len(batch) for batch in sample.iter_batches(20)) == 48
    media.requests.clear()
    with pytest.raises(TimeoutError):
        list(_FrameSample(media, time.perf_counter() - 1, 48).iter_batches(20))
    assert not media.requests


def test_invalid_encoding_fails_before_output(tmp_path):
    root, mapping = make_hdf5(tmp_path / "input")
    source = HDF5Source(root, mapping)
    for encoding in [
        VideoEncodingConfig(codec="not_an_encoder"),
        replace(ENCODING, pixel_format="not_a_pixel_format"),
    ]:
        with pytest.raises(ValueError, match="Cannot use video encoder"):
            convert(
                source,
                tmp_path / "output",
                "v3.0",
                config=ConversionConfig(video_encoding=encoding),
            )
        assert not (tmp_path / "output").exists()
        assert not list(tmp_path.glob(".output.letools-*"))
    for kwargs in [{"batch_frames": 0}, {"codec_threads": -1}]:
        with pytest.raises(ValueError, match="positive"):
            VideoEncodingConfig(**kwargs)


@pytest.mark.parametrize("command", [["convert"], ["plan"], ["dist", "plan"]])
def test_remux_cli_rejects_explicit_encoding(tmp_path, command):
    source = make_v21(tmp_path / "input")
    argv = command + [
        str(source),
        str(tmp_path / "output"),
        "--to",
        "v3.0",
        "--video-codec",
        "mjpeg",
    ]
    if command == ["dist", "plan"]:
        argv += ["--job-dir", str(tmp_path / "job")]
    with pytest.raises(ValueError, match="not applicable"):
        main(argv)


@pytest.mark.parametrize("target", ["v2.1", "v3.0"])
@pytest.mark.parametrize("explicit_format", [None, "yuvj420p"])
def test_jpeg_pixel_metadata_and_explicit_conversion(
    tmp_path, monkeypatch, target, explicit_format
):
    from fractions import Fraction
    import test_hdf5

    def jpeg444(value, count):
        encoder = av.CodecContext.create("mjpeg", "w")
        encoder.width, encoder.height = 32, 24
        encoder.pix_fmt = "yuvj444p"
        encoder.time_base = Fraction(1, 10)
        return tuple(
            bytes(
                encoder.encode(
                    av.VideoFrame.from_ndarray(
                        np.full((24, 32, 3), value + i, dtype=np.uint8), format="rgb24"
                    )
                )[0]
            )
            for i in range(count)
        )

    monkeypatch.setattr(test_hdf5, "_make_jpegs", jpeg444)
    root, mapping = make_hdf5(tmp_path / "input")
    source = HDF5Source(root, mapping)
    output = tmp_path / "output"
    convert(
        source,
        output,
        target,
        config=ConversionConfig(
            workers=1,
            video_workers=1,
            video_encoding=VideoEncodingConfig(pixel_format=explicit_format),
        ),
    )
    check_output(output, codec="mjpeg", pixel_format=explicit_format or "yuvj444p")


def test_scheduler_cpu_accounting_for_encoders(tmp_path):
    from letools.distributed import SlurmScheduler, KubernetesScheduler

    root, mapping = make_hdf5(tmp_path / "input")
    job = tmp_path / "job"
    plan_distributed_conversion(
        hdf5_source_spec(root, mapping),
        tmp_path / "out",
        "v3.0",
        job,
        worker=WorkerConfig(1, 3, video_encoding=ENCODING),
    )
    for scheduler in (
        SlurmScheduler(cpus_per_task=4, submit=False),
        KubernetesScheduler("image", cpu="4", submit=False),
    ):
        with pytest.raises(ValueError, match="concurrency"):
            scheduler.submit(job)


def test_transcoded_v3_can_be_split_without_reencoding(tmp_path):
    root, mapping = make_hdf5(tmp_path / "input")
    source = HDF5Source(root, mapping)
    v30, v21 = tmp_path / "v30", tmp_path / "v21"
    convert(
        source,
        v30,
        "v3.0",
        config=ConversionConfig(workers=1, video_workers=1, video_encoding=ENCODING),
    )
    convert(v30, v21, "v2.1", config=ConversionConfig(workers=1, video_workers=1))
    check_output(v21)
    from letools import compare_datasets

    assert compare_datasets(v30, v21, check_videos=True).equal


def test_h264_encoder_name_metadata_and_roundtrip_when_available(tmp_path):
    try:
        av.Codec("libx264", "w")
    except av.codec.codec.UnknownCodecError:
        pytest.skip("PyAV runtime does not supply libx264")
    root, mapping = make_hdf5(tmp_path / "input")
    source = HDF5Source(root, mapping)
    v30, v21 = tmp_path / "v30", tmp_path / "v21"
    encoding = replace(ENCODING, codec="libx264")
    convert(
        source,
        v30,
        "v3.0",
        config=ConversionConfig(workers=1, video_workers=1, video_encoding=encoding),
    )
    check_output(v30, codec="h264")
    convert(v30, v21, "v2.1", config=ConversionConfig(workers=1, video_workers=1))
    check_output(v21, codec="h264")
    from letools import compare_datasets

    assert compare_datasets(v30, v21, check_videos=True).equal
