import json
from pathlib import Path

from letools.cli import main
from letools.doctor import environment_report
from letools.external import upstream_project, visualizer_patches, visualizer_source
from test_roundtrip import make_v21


def test_environment_report() -> None:
    report = environment_report()
    assert report["letools"] == "0.1.0"
    assert report["native"]["provider"] in {"python", "letools-native"}
    assert "libavformat" in report["pyav"]["ffmpeg_libraries"]
    assert report["external"]["lerobot-doctor"]["commit"].startswith("ff94753")


def test_external_provenance_and_visualizer_resources() -> None:
    doctor = upstream_project("lerobot-doctor")
    assert doctor["license"] == "Apache-2.0"
    assert (visualizer_source() / "package.json").is_file()
    assert [patch.name for patch in visualizer_patches()] == [
        "0001-configurable-doctor-url.patch"
    ]


def test_doctor_environment_explicit(capsys) -> None:
    assert main(["doctor", "environment"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["external"]["lerobot-dataset-visualizer"]["commit"].startswith(
        "dc59887"
    )


def test_vendored_doctor_check(tmp_path: Path, capsys) -> None:
    dataset = make_v21(tmp_path / "dataset")
    status = main(
        [
            "doctor",
            "check",
            str(dataset),
            "--checks",
            "metadata",
            "--json",
        ]
    )
    output = json.loads(capsys.readouterr().out)
    assert status in {0, 1}
    assert output["dataset_path"] == str(dataset)
    assert [result["name"] for result in output["checks"]] == [
        "Metadata & Format Compliance"
    ]
