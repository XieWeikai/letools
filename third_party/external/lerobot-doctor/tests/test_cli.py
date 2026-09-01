"""Tests for CLI interface."""

import json

import pytest

from lerobot_doctor.cli import main
from tests.conftest import create_dataset


def test_cli_basic(tmp_dataset, capsys):
    main([str(tmp_dataset)])
    captured = capsys.readouterr()
    assert "Summary" in captured.out or "PASS" in captured.out


def test_cli_json_output(tmp_dataset, capsys):
    main([str(tmp_dataset), "--json"])
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert "checks" in data
    assert "overall_severity" in data


def test_cli_specific_checks(tmp_dataset, capsys):
    main([str(tmp_dataset), "--checks", "metadata,temporal"])
    captured = capsys.readouterr()
    assert "Metadata" in captured.out
    assert "Temporal" in captured.out


def test_cli_max_episodes(tmp_dataset, capsys):
    main([str(tmp_dataset), "--max-episodes", "1"])
    captured = capsys.readouterr()
    assert "Summary" in captured.out or "PASS" in captured.out


def test_cli_verbose(tmp_dataset, capsys):
    main([str(tmp_dataset), "-v"])
    captured = capsys.readouterr()
    assert len(captured.out) > 0


def test_cli_nonexistent_path(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["/nonexistent/path/that/does/not/exist"])
    assert exc_info.value.code == 1


def test_cli_version(capsys):
    from lerobot_doctor import __version__
    with pytest.raises(SystemExit) as exc_info:
        main(["--version"])
    captured = capsys.readouterr()
    assert __version__ in captured.out


def test_cli_ci_mode(tmp_dataset, capsys):
    main([str(tmp_dataset), "--ci"])
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert "checks" in data
    assert "pass" in captured.err or "warn" in captured.err or "fail" in captured.err


def test_cli_ci_fail_on_warn_passes_clean(tmp_dataset, capsys):
    """Clean dataset should pass even with --fail-on=warn."""
    # tmp_dataset is clean, should be all PASS
    main([str(tmp_dataset), "--ci", "--fail-on=warn", "--checks", "metadata"])
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["overall_severity"] == "PASS"


def test_cli_markdown_output(tmp_dataset, tmp_path, capsys):
    out = tmp_path / "report.md"
    main([str(tmp_dataset), "--markdown", str(out)])
    assert out.exists()
    text = out.read_text()
    assert "# lerobot-doctor report" in text
    assert "| Check | Severity | Messages |" in text


def test_cli_ci_fail_on_fail(tmp_dataset, capsys):
    """Default --fail-on=fail should exit 0 for WARN."""
    main([str(tmp_dataset), "--ci", "--fail-on=fail"])
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert "overall_severity" in data


def test_cli_gate_on_zip(tmp_path, capsys):
    import zipfile

    from tests.conftest import create_consolidated_v3_dataset

    root = create_consolidated_v3_dataset(
        tmp_path / "dataset", n_episodes=12, n_frames_per_ep=100, fps=10,
    )
    zip_path = tmp_path / "dataset.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for path in root.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(root.parent))

    main(["gate", str(zip_path), "--policy", "act"])
    captured = capsys.readouterr()
    assert "Training Gate [PASS]" in captured.out
    assert "No info.json" not in captured.out
