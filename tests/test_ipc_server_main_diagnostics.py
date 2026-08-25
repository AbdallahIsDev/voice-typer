"""Regression test for CR-40: diagnostic-write failures must NOT be silently swallowed.

CR-40 background
----------------
``voice_typer/server/ipc_server.py::main()`` writes a diagnostic
``startup-error.log`` to the config dir when ``VoiceTyperApp()`` (or
``app.start()``) raises. Previously the secondary write itself was
wrapped in ``except Exception: pass`` — so if the config dir was
read-only (e.g. a locked-down kiosk, a misconfigured AppImage, or a
pythonw.exe run where stdout/stderr are devnull), the traceback was
lost forever and the user saw only "Python process exited: 1".

The fix (CR-40) replaces the bare ``pass`` with a defense-in-depth
fallback chain::

    except Exception as write_exc:
        print(buf.getvalue(), file=sys.stderr)        # 1st fallback
        try:
            tmp = Path(tempfile.gettempdir()) / "voice-typer-startup-error.log"
            tmp.write_text(buf.getvalue(), ...)        # 2nd fallback
            log.error("[FATAL] Could not write %s; wrote to %s instead ...",
                      diag_path, tmp, write_exc)
        except Exception:
            log.error("[FATAL] Could not write diagnostic anywhere: %s",
                      write_exc)

These tests pin both fallbacks so a future refactor cannot silently
restore the swallowed-traceback bug.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# Force-import the modules whose attributes we need to patch. ``mock.patch``
# with a dotted string target uses attribute lookup on the parent module, so
# the parent module must already be in ``sys.modules`` for the patch to find
# the attribute. Importing them at collection time is harmless and makes the
# patches robust regardless of test-collection order.
import voice_typer.server.app  # noqa: F401
import voice_typer.server.config  # noqa: F401
import voice_typer.server.ipc_server  # noqa: F401


def _boom(*_args: object, **_kwargs: object) -> None:
    """Side effect that simulates a fatal failure in VoiceTyperApp()."""
    raise RuntimeError("simulated VoiceTyperApp() construction failure")


def _patch_main_dependencies(config_dir: Path):
    """Patch every symbol ``main()`` touches before reaching VoiceTyperApp().

    Returns a single context-manager that stacks all the patches so test
    bodies stay readable.
    """
    return (
        patch("voice_typer.server.ipc_server._set_process_metadata"),
        patch("voice_typer.server.logging_setup._setup_logging"),
        patch(
            "voice_typer.server.single_instance._ensure_single_instance",
            return_value=None,
        ),
        patch("voice_typer.server.app.VoiceTyperApp", side_effect=_boom),
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
    # mocked) regardless of any ambient env from a prior test.
    monkeypatch.delenv("TAURI_SIDECAR", raising=False)


class TestStartupDiagnosticsFallback:
    """CR-40: ``main()`` must never swallow the diagnostic-write traceback."""

    def test_tempfile_fallback_when_config_dir_unwritable(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When ``_secure_atomic_write`` fails, main() must write to a tempfile.

        Asserts the fallback file at
        ``$TMPDIR/voice-typer-startup-error.log`` contains the full
        traceback — proving CR-40's second-tier fallback works.
        """
        # Redirect tempfile.gettempdir() to our tmp_path so we can
        # deterministically assert the fallback file landed there and
        # so we don't pollute the real /tmp.
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))

        # Point _config_dir() at a (nonexistent) path so any non-mocked
        # write attempt would also fail — defense in depth.
        fake_config_dir = tmp_path / "config"

        patches = _patch_main_dependencies(fake_config_dir)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            from voice_typer.server.ipc_server import main

            with pytest.raises(SystemExit) as excinfo:
                main()

        # main() exits with EXIT_CRASH (== 1) on this path.
        assert excinfo.value.code == 1

        # second-tier fallback: a tempfile was written.
        fallback_file = tmp_path / "voice-typer-startup-error.log"
        assert fallback_file.exists(), f"tempfile fallback not written; tmp_path contains: {list(tmp_path.iterdir())}"

        # The fallback file must contain the *full* diagnostic payload —
        # specifically the traceback and the simulated failure message.
        # This proves the bug is fixed: previously the traceback was
        # silently discarded by ``except Exception: pass``.
        content = fallback_file.read_text(encoding="utf-8")
        assert "Voice Typer startup failed at" in content, (
            "fallback file missing the diagnostic header — got:\n" + content
        )
        assert "Traceback" in content
        assert "simulated VoiceTyperApp() construction failure" in content

    def test_stderr_fallback_when_tempfile_unwritable(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """When both ``_secure_atomic_write`` AND the tempfile fail, main()
        must still print the traceback to stderr.

        Simulates the worst case: read-only config dir AND a tempfile dir
        that doesn't exist (e.g. a locked-down container with a missing
        ``$TMPDIR``). The third-tier fallback — ``print(buf, file=sys.stderr)``
        — must still surface the traceback so the user (or test harness)
        can see why the process died.
        """
        # Make tempfile.gettempdir() return a directory that does NOT
        # exist on disk so Path.write_text() raises FileNotFoundError.
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

        # third-tier fallback: stderr must contain the traceback.
        # We assert on the unique "Voice Typer startup failed at" header
        # that only appears in ``buf.getvalue()`` (which is what the
        # ``print(buf.getvalue(), file=sys.stderr)`` line emits), NOT on
        # the "Traceback" string alone (which the prior
        # ``log.exception()`` call would also emit via the logging
        # last-resort handler, masking a regression).
        captured = capsys.readouterr()
        stderr_text = captured.err
        assert "Voice Typer startup failed at" in stderr_text, (
            "stderr fallback did not fire; stderr was:\n" + stderr_text
        )
        assert "simulated VoiceTyperApp() construction failure" in stderr_text

        # The tempfile fallback must NOT have been created in this case
        # (its target directory doesn't exist). Asserting the negative
        # confirms we're exercising the *third*-tier path, not a leak
        # of the second-tier one.
        assert not nonexistent_tmp.exists()
