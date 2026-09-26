"""Regression test for CR-40: diagnostic-write failures must NOT be silently swallowed."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# Force-import the modules whose attributes we need to patch. ``mock.patch``
import voice_typer.server.app  # noqa: F401
import voice_typer.server.config  # noqa: F401
import voice_typer.server.ipc_server  # noqa: F401


def _boom(*_args: object, **_kwargs: object) -> None:
    """Side effect that simulates a fatal failure in LausuApp()."""
    raise RuntimeError("simulated LausuApp() construction failure")


def _patch_main_dependencies(config_dir: Path):
    """Patch every symbol ``main()`` touches before reaching LausuApp()."""
    return (
        patch("voice_typer.server.ipc_server._set_process_metadata"),
        patch("voice_typer.server.logging_setup._setup_logging"),
        patch(
            "voice_typer.server.single_instance._ensure_single_instance",
            return_value=None,
        ),
        patch("voice_typer.server.app.LausuApp", side_effect=_boom),
        patch(
            "voice_typer.server.config._secure_atomic_write",
            side_effect=OSError("read-only filesystem"),
        ),
        patch(
            "voice_typer.server.config._config_dir",
            return_value=config_dir,
        ),
    )


@pytest.fixture(autouse=True)
def _clean_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() parses sys.argv via argparse; keep it clean for reproducibility."""
    monkeypatch.setattr(sys, "argv", ["ipc_server"])
    # Also clear TAURI_SIDECAR so _ensure_single_instance() runs (and gets
    monkeypatch.delenv("TAURI_SIDECAR", raising=False)


class TestStartupDiagnosticsFallback:
    """``main()`` must never swallow the diagnostic-write traceback."""

    def test_tempfile_fallback_when_config_dir_unwritable(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When ``_secure_atomic_write`` fails, main() must write to a tempfile."""
        # Redirect tempfile.gettempdir() to our tmp_path so we can
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))

        # Point _config_dir() at a (nonexistent) path so any non-mocked
        fake_config_dir = tmp_path / "config"

        patches = _patch_main_dependencies(fake_config_dir)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            from voice_typer.server.ipc_server import main

            with pytest.raises(SystemExit) as excinfo:
                main()

        assert excinfo.value.code == 1

        fallback_file = tmp_path / "lausu-startup-error.log"
        assert fallback_file.exists(), f"tempfile fallback not written; tmp_path contains: {list(tmp_path.iterdir())}"

        # The fallback file must contain the *full* diagnostic payload —
        content = fallback_file.read_text(encoding="utf-8")
        assert "Lausu startup failed at" in content, "fallback file missing the diagnostic header, got:\n" + content
        assert "Traceback" in content
        assert "simulated LausuApp() construction failure" in content

    def test_stderr_fallback_when_tempfile_unwritable(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """When both ``_secure_atomic_write`` AND the tempfile fail, main()"""
        # Make tempfile.gettempdir() return a directory that does NOT
        nonexistent_tmp = tmp_path / "does-not-exist"
        assert not nonexistent_tmp.exists()
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(nonexistent_tmp))

        fake_config_dir = tmp_path / "config"

        patches = _patch_main_dependencies(fake_config_dir)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            from voice_typer.server.ipc_server import main

            with pytest.raises(SystemExit) as excinfo:
                main()

        assert excinfo.value.code == 1

        captured = capsys.readouterr()
        stderr_text = captured.err
        assert "Lausu startup failed at" in stderr_text, "stderr fallback did not fire; stderr was:\n" + stderr_text
        assert "simulated LausuApp() construction failure" in stderr_text

        # The tempfile fallback must NOT have been created in this case
        assert not nonexistent_tmp.exists()
