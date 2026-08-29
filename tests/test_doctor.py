from letools.doctor import environment_report


def test_environment_report() -> None:
    report = environment_report()
    assert report["letools"] == "0.1.0"
    assert report["native"]["provider"] in {"python", "letools-native"}
    assert "libavformat" in report["pyav"]["ffmpeg_libraries"]
