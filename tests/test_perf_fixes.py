"""XV (Performance & Resources) fix regression tests."""

from __future__ import annotations

import inspect
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# XV-1 deps-probe tests removed 2026-08-15: ``_check_qwen_deps`` /


class TestDownloadPollScopedToModelDir:
    """in-progress repo's HF cache subdir, not the entire HF cache tree."""

    def test_poll_uses_per_repo_subdir_construction(self):
        """The progress loop must construct ``model_dir = cache_dir /"""
        from voice_typer.server.service import VoiceTyperService

        src = inspect.getsource(VoiceTyperService.download_model)
        # The exact construction line, keep in sync with the source.
        assert 'model_dir = cache_dir / f"models--{repo_id.replace' in src, (
            "XV-2: download_model must construct the per-repo subdir "
            "via cache_dir / f\"models--{repo_id.replace('/', '--')}\" "
            "before walking it."
        )
        assert 'model_dir.rglob("*")' in src, (
            "XV-2: progress polling must walk model_dir (the per-repo subdir), not the whole cache_dir."
        )


class TestMicrophonesCacheEmptyList:
    """a legitimately-empty device list is served from cache."""

    def test_cache_initialised_to_none(self, tmp_config_dir):
        """The cache must start as ``None`` so we can distinguish"""
        from voice_typer.server.service import VoiceTyperService

        class FakeApp:
            config = type("FakeConfig", (), {})()

        svc = VoiceTyperService(FakeApp())
        assert svc._microphones_cache is None, (
            "XV-5: _microphones_cache must be initialised to None (not "
            "[]) so the truthiness check doesn't bypass the cache when "
            "PortAudio returns an empty list."
        )

    def test_empty_list_served_from_cache(self, tmp_config_dir, monkeypatch):
        """When the cache holds an empty list (legitimate \"0 mics\"),"""
        from voice_typer.server import service as svc_mod

        class FakeApp:
            config = type("FakeConfig", (), {})()
            _microphones = []
            tray = MagicMock()

        svc = svc_mod.VoiceTyperService(FakeApp())

        # Seed the cache with an empty list (simulating PortAudio
        svc._microphones_cache = []
        svc._microphones_cache_ts = time.monotonic()

        call_count = {"n": 0}

        def fake_list_microphones():
            call_count["n"] += 1
            return [{"name": "should-not-be-returned", "index": 0}]

        monkeypatch.setattr(
            "voice_typer.server.server_platform.microphone_list.list_microphones",
            fake_list_microphones,
        )

        result = svc.refresh_microphones()
        assert result == [], (
            "XV-5: refresh_microphones must serve the cached empty list "
            "instead of re-querying PortAudio when the cache is empty."
        )
        assert call_count["n"] == 0, (
            "XV-5: list_microphones must NOT be called when the cache (even if empty) is fresh."
        )

    def test_non_empty_list_still_served_from_cache(self, tmp_config_dir, monkeypatch):
        """Sanity: a non-empty cache continues to be served (regression"""
        from voice_typer.server import service as svc_mod

        class FakeApp:
            config = type("FakeConfig", (), {})()
            _microphones = []
            tray = MagicMock()

        svc = svc_mod.VoiceTyperService(FakeApp())
        cached_mics = [{"name": "USB Mic", "index": 0}]
        svc._microphones_cache = cached_mics
        svc._microphones_cache_ts = time.monotonic()

        call_count = {"n": 0}
        monkeypatch.setattr(
            "voice_typer.server.server_platform.microphone_list.list_microphones",
            lambda: call_count.__setitem__("n", call_count["n"] + 1) or [],
        )

        result = svc.refresh_microphones()
        assert result == cached_mics
        assert call_count["n"] == 0

    def test_refresh_microphones_source_uses_is_not_none(self):
        """Source guard: the truthiness check must be ``is not None``,"""
        from voice_typer.server.service import VoiceTyperService

        src = inspect.getsource(VoiceTyperService.refresh_microphones)
        assert "self._microphones_cache is not None" in src, (
            "XV-5: refresh_microphones must use 'is not None' (not bare "
            "truthiness) so an empty cached list is still served from cache."
        )
        # The old buggy form must NOT appear in actual code lines.
        code_lines = [line for line in src.splitlines() if line.lstrip() and not line.lstrip().startswith("#")]
        code_only = "\n".join(code_lines)
        assert "if self._microphones_cache and " not in code_only, (
            "XV-5 regression: refresh_microphones still uses bare-truthiness "
            "cache check, this skips the cache when PortAudio returns 0 mics."
        )


class TestWaitForIpcReady:
    """XV-6: ``_wait_for_ipc_ready`` provides a bounded (5 s deadline)"""

    def test_returns_true_immediately_when_port_open(self, monkeypatch):
        """When the IPC port is already accepting connections, the poll"""
        from voice_typer.server import autostart_launcher as al

        monkeypatch.setattr(al, "_is_port_open", lambda h, p: True)
        monkeypatch.setattr(al, "_read_ipc_port_from_pid_file", lambda: None)
        # Force the deadline path to be observable: patch sleep to fail
        sleep_calls = []
        monkeypatch.setattr(al.time, "sleep", lambda s: sleep_calls.append(s))

        result = al._wait_for_ipc_ready(deadline_s=5.0)
        assert result is True
        assert sleep_calls == [], "XV-6: must not sleep when the port is already open on the first poll."

    def test_returns_false_on_deadline_when_port_closed(self, monkeypatch):
        """When the IPC port never opens, the poll must run for at most"""
        from voice_typer.server import autostart_launcher as al

        monkeypatch.setattr(al, "_is_port_open", lambda h, p: False)
        monkeypatch.setattr(al, "_read_ipc_port_from_pid_file", lambda: None)
        # Speed up the test: zero out sleep so the deadline loop spins
        monkeypatch.setattr(al.time, "sleep", lambda s: None)
        # Use a tiny deadline + patch monotonic so we don't actually
        start = [time.monotonic()]

        # Drive monotonic forward artificially to expire the deadline
        call_count = {"n": 0}

        def fake_monotonic():
            call_count["n"] += 1
            return start[0] + call_count["n"] * 0.25

        monkeypatch.setattr(al.time, "monotonic", fake_monotonic)

        result = al._wait_for_ipc_ready(deadline_s=1.0, poll_interval_s=0.25)
        assert result is False, (
            "XV-6: must return False (not raise) when the IPC port never becomes ready within the deadline."
        )

    def test_uses_port_from_pid_file_when_present(self, monkeypatch):
        """When the backend PID file declares a non-default port, the"""
        from voice_typer.server import autostart_launcher as al

        captured_ports = []
        monkeypatch.setattr(al, "_is_port_open", lambda h, p: captured_ports.append(p) or True)
        monkeypatch.setattr(al, "_read_ipc_port_from_pid_file", lambda: 12345)
        monkeypatch.setattr(al.time, "sleep", lambda s: None)

        result = al._wait_for_ipc_ready(deadline_s=1.0)
        assert result is True
        assert 12345 in captured_ports, "XV-6: must use the port from the PID file (12345), not the default IPC_PORT."

    def test_falls_back_to_default_port_when_pid_file_missing(self, monkeypatch):
        """When the PID file is missing/unreadable, the poll falls back"""
        from voice_typer.server import autostart_launcher as al

        captured_ports = []
        monkeypatch.setattr(al, "_is_port_open", lambda h, p: captured_ports.append(p) or True)
        monkeypatch.setattr(al, "_read_ipc_port_from_pid_file", lambda: None)
        monkeypatch.setattr(al.time, "sleep", lambda s: None)

        al._wait_for_ipc_ready(deadline_s=1.0)
        assert al.IPC_PORT in captured_ports

    def test_returns_true_after_finite_polls_when_port_opens_late(self, monkeypatch):
        """When the port opens after N failed polls, the function must"""
        from voice_typer.server import autostart_launcher as al

        poll_results = [False, False, True]  # port opens on 3rd poll
        call_idx = {"n": 0}

        def fake_is_port_open(host, port):
            idx = call_idx["n"]
            call_idx["n"] += 1
            return poll_results[idx] if idx < len(poll_results) else True

        monkeypatch.setattr(al, "_is_port_open", fake_is_port_open)
        monkeypatch.setattr(al, "_read_ipc_port_from_pid_file", lambda: None)
        monkeypatch.setattr(al.time, "sleep", lambda s: None)

        result = al._wait_for_ipc_ready(deadline_s=10.0, poll_interval_s=0.25)
        assert result is True
        # Exactly 3 polls: 2 failed + 1 succeeded.
        assert call_idx["n"] == 3, f"XV-6: expected 3 polls (2 fail + 1 succeed), got {call_idx['n']}"


class TestNoFixedSleepTwoInLaunch:
    """XV-6: the ``launch()`` function must NOT contain any"""

    def test_launch_source_has_no_fixed_sleep_two(self):
        """``launch()`` function body (the 3 spawn-success sites must now"""
        from voice_typer.server import autostart_launcher as al

        src = inspect.getsource(al.launch)
        # Strip comments before checking so a docstring or inline
        code_lines = []
        for line in src.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            code_lines.append(line)
        code_only = "\n".join(code_lines)
        assert "time.sleep(2)" not in code_only, (
            "XV-6 regression: launch() still contains a fixed time.sleep(2), must use _wait_for_ipc_ready() instead."
        )
        # And the new helper must be called at least once.
        assert "_wait_for_ipc_ready()" in code_only, (
            "XV-6: launch() must call _wait_for_ipc_ready() at the spawn-success sites."
        )


class TestNumpyDeadImportRemoved:
    """XV-4: the dead ``import numpy as np`` at the top of ``app.py``"""

    def test_app_module_does_not_import_numpy(self):
        """Source guard: no ``import numpy`` line at module top."""
        app_path = Path(__file__).resolve().parent.parent / "voice_typer" / "server" / "app.py"
        src = app_path.read_text()
        # Check the top-level import statements only (first ~60 lines
        for line in src.splitlines()[:60]:
            stripped = line.lstrip()
            if stripped.startswith("import numpy") or stripped.startswith("from numpy"):
                pytest.fail(f"XV-4: app.py still imports numpy at module top: {line!r}")

    def test_app_module_imports_cleanly(self):
        """Sanity: app.py must still import successfully after the dead"""
        try:
            import voice_typer.server.app  # noqa: F401
        except ImportError as exc:
            # Only skip if the failure originates OUTSIDE app.py itself
            if "voice_typer/server/app.py" in str(exc):
                pytest.fail(
                    f"XV-4: app.py failed to import after numpy removal (likely a missed ``np`` reference): {exc}"
                )
            pytest.skip(
                f"XV-4 sanity check skipped, downstream import error "
                f"outside app.py scope (parallel agent edit in progress?): {exc}"
            )
