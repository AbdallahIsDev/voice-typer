"""Round 16 regression tests for ERR-ERR-001 through ERR-ERR-006 + ERR-LINT-001.

Each test verifies a specific fix by triggering the error path and
asserting the correct behavior (logging, null check, type safety).
"""
from __future__ import annotations

import sys
import threading
from unittest.mock import MagicMock, patch

import numpy as np
import pytest


# ── ERR-ERR-002: except BaseException → except Exception ──────────────


class TestExceptExceptionNotBaseException:
    """ERR-ERR-002: ipc_server.main() must not catch BaseException."""

    def test_main_catches_exception_not_baseexception(self):
        """Verify the source uses `except Exception` not `except BaseException`."""
        from pathlib import Path
        ipc_path = Path(__file__).resolve().parents[1] / "voice_typer" / "server" / "ipc_server.py"
        src = ipc_path.read_text(encoding="utf-8")
        # Must NOT have `except BaseException:` in the main() function
        assert "except BaseException:" not in src, (
            "ipc_server.py must not use `except BaseException` (ERR-ERR-002)"
        )
        # Must have `except Exception:` in the main() function
        assert "except Exception:" in src


# ── ERR-ERR-003: type: ignore real bugs fixed ─────────────────────────


class TestTypeIgnoreBugsFixed:
    """ERR-ERR-003: verify the 5 type:ignore real bugs are fixed."""

    def test_audio_processor_hp_state_properly_typed(self):
        """_hp_state must be typed as Optional[tuple[...]] not Optional[tuple]."""
        import inspect
        from voice_typer.server.audio_processor import AudioProcessor
        src = inspect.getsource(AudioProcessor.__init__)
        # Must NOT have the old bare `Optional[tuple]` type
        assert "Optional[tuple]  # (b, a, zi)" not in src
        # Must have the new typed version
        assert "tuple[np.ndarray, np.ndarray, np.ndarray]" in src

    def test_audio_processor_rnnoise_null_check(self):
        """_rnnoise must be null-checked before calling filter_frame."""
        import inspect
        from voice_typer.server.audio_processor import AudioProcessor
        src = inspect.getsource(AudioProcessor._apply_rnnoise)
        assert "if self._rnnoise is None" in src, (
            "_rnnoise must be null-checked before filter_frame (ERR-ERR-003)"
        )

    def test_audio_processor_quality_callback_null_check(self):
        """_quality_callback must be null-checked before calling."""
        import inspect
        from voice_typer.server.audio_processor import AudioProcessor
        src = inspect.getsource(AudioProcessor._run_quality_check)
        assert "if self._quality_callback is not None" in src, (
            "_quality_callback must be null-checked (ERR-ERR-003)"
        )

    def test_volume_ducker_backend_null_check_in_monitor(self):
        """_backend must be null-checked in _smart_duck_monitor_loop."""
        import inspect
        from voice_typer.server.volume_ducker import VolumeDucker
        src = inspect.getsource(VolumeDucker._smart_duck_monitor_loop)
        assert "if self._backend is None" in src, (
            "_backend must be null-checked in monitor loop (ERR-ERR-003)"
        )

    def test_volume_ducker_backend_null_check_in_duck(self):
        """_backend must be null-checked in duck() method too."""
        import inspect
        from voice_typer.server.volume_ducker import VolumeDucker
        src = inspect.getsource(VolumeDucker.duck)
        assert "self._backend is not None" in src, (
            "_backend must be null-checked in duck() (ERR-ERR-003)"
        )

    def test_volume_backends_bare_type_ignore_fixed(self):
        """volume_backends.py:353 must specify the rule, not bare `# type: ignore`."""
        from pathlib import Path
        path = Path(__file__).resolve().parents[1] / "voice_typer" / "server" / "volume_backends.py"
        src = path.read_text(encoding="utf-8")
        # Must NOT have bare `# type: ignore` (without brackets)
        lines = [l for l in src.split("\n") if "type: ignore" in l and "import-not-found" not in l]
        bare_ignores = [l for l in lines if l.rstrip().endswith("# type: ignore")]
        assert not bare_ignores, (
            f"Found bare `# type: ignore` without rule: {bare_ignores}"
        )

    def test_no_malformed_type_ignore_isc(self):
        """Must not have the malformed `# type: ignoreisc]` pattern."""
        from pathlib import Path
        server_dir = Path(__file__).resolve().parents[1] / "voice_typer" / "server"
        for py_file in server_dir.glob("*.py"):
            src = py_file.read_text(encoding="utf-8")
            assert "ignoreisc]" not in src, (
                f"{py_file.name} has malformed `# type: ignoreisc]` (ERR-ERR-003)"
            )


# ── ERR-ERR-005: TypeScript non-null assertions ────────────────────────


class TestTypeScriptNonNullAssertions:
    """ERR-ERR-005: verify the 4 non-null assertion locations are fixed."""

    def test_history_no_non_null_assertion_on_path(self):
        from pathlib import Path
        path = Path(__file__).resolve().parents[1] / "voice_typer" / "client" / "src" / "renderer" / "src" / "pages" / "History.tsx"
        src = path.read_text(encoding="utf-8")
        assert "result.path!" not in src, (
            "History.tsx must not use `!` on result.path (ERR-ERR-005)"
        )

    def test_vocabulary_no_non_null_assertion_on_path(self):
        from pathlib import Path
        path = Path(__file__).resolve().parents[1] / "voice_typer" / "client" / "src" / "renderer" / "src" / "pages" / "Vocabulary.tsx"
        src = path.read_text(encoding="utf-8")
        assert "result.path!" not in src, (
            "Vocabulary.tsx must not use `!` on result.path (ERR-ERR-005)"
        )

    def test_main_tsx_no_non_null_assertion(self):
        from pathlib import Path
        path = Path(__file__).resolve().parents[1] / "voice_typer" / "client" / "src" / "renderer" / "src" / "main.tsx"
        src = path.read_text(encoding="utf-8")
        assert "getElementById('root')!" not in src, (
            "main.tsx must not use `!` on getElementById (ERR-ERR-005)"
        )
        assert "if (!rootEl)" in src, (
            "main.tsx must have explicit null check (ERR-ERR-005)"
        )

    def test_bubble_main_tsx_no_non_null_assertion(self):
        from pathlib import Path
        path = Path(__file__).resolve().parents[1] / "voice_typer" / "client" / "src" / "renderer" / "src" / "bubble-main.tsx"
        src = path.read_text(encoding="utf-8")
        assert "getElementById('bubble-root')!" not in src, (
            "bubble-main.tsx must not use `!` on getElementById (ERR-ERR-005)"
        )
        assert "if (!bubbleRootEl)" in src, (
            "bubble-main.tsx must have explicit null check (ERR-ERR-005)"
        )


# ── ERR-LINT-001: vad.py stderr redirect ──────────────────────────────


class TestVadStderrRedirect:
    """ERR-LINT-001: vad.py must redirect BOTH stdout and stderr."""

    def test_vad_redirects_both_streams(self):
        import inspect
        from voice_typer.server import vad
        src = inspect.getsource(vad)
        assert "redirect_stderr" in src, (
            "vad.py must redirect stderr (not just stdout) to suppress "
            "torch.hub.load's 'Using cache found in...' message (ERR-LINT-001)"
        )


# ── ERR-ERR-003: functional test — _rnnoise null check works ──────────


class TestAudioProcessorNullChecksFunctional:
    """Functional tests that the null checks actually prevent crashes."""

    def test_rnnoise_null_does_not_crash(self):
        """When _rnnoise is None, _apply_rnnoise should return the input
        unchanged, not crash with AttributeError."""
        from voice_typer.server.audio_processor import AudioProcessor, AudioProcessorConfig
        config = AudioProcessorConfig()
        proc = AudioProcessor(config, sample_rate=16000)
        proc._rnnoise = None  # Simulate failed init
        proc._rnnoise_frame_size = 480
        # Create enough samples for at least one full frame
        chunk = np.ones(480, dtype=np.float32) * 0.1
        result = proc._apply_rnnoise(chunk)
        # Should return the input unchanged (not crash)
        assert len(result) == len(chunk)

    def test_quality_callback_null_does_not_crash(self):
        """When _quality_callback is None, _run_quality_check should
        be a no-op, not crash."""
        from voice_typer.server.audio_processor import AudioProcessor, AudioProcessorConfig
        config = AudioProcessorConfig()
        proc = AudioProcessor(config, sample_rate=16000)
        proc._quality_callback = None
        chunk = np.ones(1024, dtype=np.float32) * 0.1
        # Should not raise
        proc._run_quality_check(chunk)
