"""DJ-49: multi-process log race: prewarm + main must write different files."""

from __future__ import annotations

import logging
from pathlib import Path

from voice_typer.server.log import (
    get_log_file_path,
    reset,
    setup_logging,
)


def _flush_handlers() -> None:
    """Flush all voice_typer handlers so file content is on disk."""
    import contextlib

    for h in logging.getLogger("voice_typer").handlers:
        with contextlib.suppress(Exception):
            h.flush()


def test_main_process_writes_to_voice_typer_log(tmp_path: Path) -> None:
    """Default ``process_name=\"main\"`` writes to ``voice-typer.log``."""
    reset()
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    try:
        setup_logging(config_dir)
        log = logging.getLogger("voice_typer.server.dj49_test_main")
        log.info("[DJ-49] main-process test line")
        _flush_handlers()

        main_log = config_dir / "logs" / "voice-typer.log"
        prewarm_log = config_dir / "logs" / "prewarm.log"

        assert main_log.exists(), "main log file should exist after setup_logging"
        assert not prewarm_log.exists(), "prewarm log file should NOT exist when process_name is 'main'"
        content = main_log.read_text(encoding="utf-8")
        assert "[DJ-49] main-process test line" in content
    finally:
        reset()


def test_prewarm_process_writes_to_prewarm_log(tmp_path: Path) -> None:
    """``process_name=\"prewarm\"`` writes to ``prewarm.log``."""
    reset()
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    try:
        setup_logging(config_dir, process_name="prewarm")
        log = logging.getLogger("voice_typer.server.dj49_test_prewarm")
        log.info("[DJ-49] prewarm-process test line")
        _flush_handlers()

        main_log = config_dir / "logs" / "voice-typer.log"
        prewarm_log = config_dir / "logs" / "prewarm.log"

        assert prewarm_log.exists(), "prewarm log file should exist when process_name='prewarm'"
        assert not main_log.exists(), (
            "main log file should NOT exist when process_name='prewarm', "
            "this is the core race-elimination invariant (prewarm must not "
            "touch the shared voice-typer.log)"
        )
        content = prewarm_log.read_text(encoding="utf-8")
        assert "[DJ-49] prewarm-process test line" in content
    finally:
        reset()


def test_main_and_prewarm_paths_are_disjoint(tmp_path: Path) -> None:
    """The two process_name values resolve to DIFFERENT file paths."""
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()

    main_path = get_log_file_path(config_dir, process_name="main")
    prewarm_path = get_log_file_path(config_dir, process_name="prewarm")

    assert main_path == config_dir / "logs" / "voice-typer.log"
    assert prewarm_path == config_dir / "logs" / "prewarm.log"
    assert main_path != prewarm_path, (
        "DJ-49 invariant violated: main and prewarm must write to DIFFERENT "
        "files (otherwise the RotatingFileHandler race re-emerges)"
    )


def test_get_log_file_path_defaults_to_main(tmp_path: Path) -> None:
    """``get_log_file_path(config_dir)`` defaults to ``voice-typer.log``."""
    config_dir = tmp_path / "cfg"
    default_path = get_log_file_path(config_dir)
    explicit_main_path = get_log_file_path(config_dir, process_name="main")

    assert default_path == config_dir / "logs" / "voice-typer.log"
    assert explicit_main_path == default_path


def test_get_log_file_path_unknown_process_name_falls_back_to_main(
    tmp_path: Path,
) -> None:
    """An unrecognised ``process_name`` falls back to the main log path."""
    config_dir = tmp_path / "cfg"
    unknown_path = get_log_file_path(config_dir, process_name="renderer")
    main_path = get_log_file_path(config_dir, process_name="main")

    assert unknown_path == main_path, (
        "unknown process_name should fall back to the main log path, not introduce a new filename"
    )
