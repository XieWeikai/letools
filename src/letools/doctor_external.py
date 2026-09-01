"""Compatibility adapter for the complete vendored lerobot-doctor CLI."""

from __future__ import annotations

from collections.abc import Sequence


def run_doctor(arguments: Sequence[str]) -> int:
    """Run upstream Doctor unchanged and translate SystemExit into a return code.

    Doctor intentionally owns parsing and output for every dataset command. A
    thin boundary prevents letools from drifting as upstream adds checks or
    curation commands while still giving the top-level CLI a normal exit code.
    """

    from lerobot_doctor.cli import main as upstream_main

    try:
        result = upstream_main(list(arguments))
    except SystemExit as error:
        code = error.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        return 1
    return int(result or 0)


__all__ = ["run_doctor"]
