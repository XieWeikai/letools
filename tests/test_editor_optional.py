"""Base installs remain usable without the independently installed editor."""

import builtins

from letools.cli import build_parser, main


def test_optional_editor_dispatch_reports_install_command(monkeypatch, capsys):
    original = builtins.__import__

    def without_editor(name, *args, **kwargs):
        if name.startswith("letools_editor"):
            error = ModuleNotFoundError("No module named 'letools_editor'")
            error.name = "letools_editor"
            raise error
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_editor)
    assert "editor" in build_parser().format_help()
    assert main(["editor", "--help"]) == 2
    assert "uv tool install" in capsys.readouterr().err
