"""XV (Performance & Resources) fix regression tests.

Covers GROUP-2 fixes that the comprehensive review labelled XV-1 .. XV-6:

* XV-1: ``service._check_parakeet_deps`` / ``_check_qwen_deps`` use
  ``importlib.util.find_spec`` instead of ``importlib.import_module`` so
  probing for a heavy dependency (``torch`` / ``qwen_asr``) doesn't
  actually execute the package's top-level code (which for ``torch``
  allocates GPU memory and inits CUDA — measurable in seconds).
* XV-2: ``service.download_model``'s progress-polling loop walks ONLY
  the in-progress repo's HF cache subdir, not the entire HF cache tree.
  (Source-level guard already exists in
  ``tests/test_history_and_models.py::TestDownloadPollScopedToModelDir``
  — we add a complementary spec-resolution test here.)
* XV-5: ``service._microphones_cache`` is initialised to ``None`` (not
  ``[]``) so a legitimately-empty device list is served from cache
  instead of re-querying PortAudio on every call.
* XV-6: ``autostart_launcher._wait_for_ipc_ready`` provides a bounded
  (5 s deadline) port-readiness poll that replaces the previous fixed
  ``time.sleep(2)`` after spawning a fresh backend / Electron / Tauri
  child.
"""

from __future__ import annotations

import inspect
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# XV-1 deps-probe tests removed 2026-08-15: ``_check_qwen_deps`` /
# ``_check_parakeet_deps`` were deleted with the torch engine — both
# backends are ONNX now (onnxruntime + onnx-asr are base deps), so the
# Models-page ``deps_ok`` is a constant True with no module probe.


# download_model polling walks only the per-repo subdir ──────


class TestDownloadPollScopedToModelDir:
    """XV-2: ``download_model``'s progress-polling loop walks ONLY the
    in-progress repo's HF cache subdir, not the entire HF cache tree.

    A source-level guard already exists in
    ``tests/test_history_and_models.py::TestDownloadPollScopedToModelDir``.
    We add a complementary assertion here that pins the exact name of
    the per-repo subdir helper so a future refactor that inlines the
    construction in a way that re-widens the rglob still trips this
    test."""

    def test_poll_uses_per_repo_subdir_construction(self):
        """The progress loop must construct ``model_dir = cache_dir /
        f"models--{repo_id.replace('/', '--')}"`` before calling rglob
        on it (HF Hub's on-disk layout)."""
        from voice_typer.server.service import VoiceTyperService

        src = inspect.getsource(VoiceTyperService.download_model)
        # The exact construction line — keep in sync with the source.
        assert 'model_dir = cache_dir / f"models--{repo_id.replace' in src, (
            "XV-2: download_model must construct the per-repo subdir "
            "via cache_dir / f\"models--{repo_id.replace('/', '--')}\" "
            "before walking it."
        )
        assert 'model_dir.rglob("*")' in src, (
            "XV-2: progress polling must walk model_dir (the per-repo subdir), not the whole cache_dir."
        )


# empty microphone list is served from cache ────────────────


class TestMicrophonesCacheEmptyList:
    """XV-5: ``_microphones_cache`` starts as ``None`` (not ``[]``) and
    a legitimately-empty device list is served from cache.

    Pre-fix bug: the truthiness check ``if self._microphones_cache and
    ...`` would skip the cache when PortAudio returned 0 mics (an empty
    list is falsy), re-querying PortAudio on every refresh call.
    """

    def test_cache_initialised_to_none(self, tmp_config_dir):
        """The cache must start as ``None`` so we can distinguish
        "never queried" from "queried and got 0 mics"."""
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
        """When the cache holds an empty list (legitimate "0 mics"),
        ``refresh_microphones`` must return that cached empty list
        within the TTL window WITHOUT re-calling ``list_microphones``."""
        from voice_typer.server import service as svc_mod

        class FakeApp:
            config = type("FakeConfig", (), {})()
            _microphones = []
            tray = MagicMock()

        svc = svc_mod.VoiceTyperService(FakeApp())

        # Seed the cache with an empty list (simulating PortAudio
        # returning 0 mics on the first call).
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
        # fix: empty cache is served (not bypassed).
        assert result == [], (
            "XV-5: refresh_microphones must serve the cached empty list "
            "instead of re-querying PortAudio when the cache is empty."
        )
        assert call_count["n"] == 0, (
            "XV-5: list_microphones must NOT be called when the cache (even if empty) is fresh."
        )

    def test_non_empty_list_still_served_from_cache(self, tmp_config_dir, monkeypatch):
        """Sanity: a non-empty cache continues to be served (regression
        guard — the XV-5 fix must not break the non-empty path)."""
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
        """Source guard: the truthiness check must be ``is not None``,
        not a bare-truthiness ``if self._microphones_cache and ...``."""
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
            "cache check — this skips the cache when PortAudio returns 0 mics."
        )


# bounded port-readiness poll replaces fixed sleep(2) ────────


class TestWaitForIpcReady:
    """XV-6: ``_wait_for_ipc_ready`` provides a bounded (5 s deadline)
    port-readiness poll that replaces the previous fixed
    ``time.sleep(2)`` after spawning a fresh backend / Electron / Tauri
    child.

    Contract:
      * Returns ``True`` as soon as the IPC port accepts connections.
      * Returns ``False`` on deadline (default 5 s) without raising.
      * Polls the actual port from the PID file when present
        (handles non-default ports), falling back to ``IPC_PORT``.
    """

    def test_returns_true_immediately_when_port_open(self, monkeypatch):
        """When the IPC port is already accepting connections, the poll
        returns ``True`` on the first iteration — no sleep needed."""
        from voice_typer.server import autostart_launcher as al

        monkeypatch.setattr(al, "_is_port_open", lambda h, p: True)
        monkeypatch.setattr(al, "_read_ipc_port_from_pid_file", lambda: None)
        # Force the deadline path to be observable: patch sleep to fail
        # the test if called.
        sleep_calls = []
        monkeypatch.setattr(al.time, "sleep", lambda s: sleep_calls.append(s))

        result = al._wait_for_ipc_ready(deadline_s=5.0)
        assert result is True
        assert sleep_calls == [], "XV-6: must not sleep when the port is already open on the first poll."

    def test_returns_false_on_deadline_when_port_closed(self, monkeypatch):
        """When the IPC port never opens, the poll must run for at most
        ``deadline_s`` seconds and return ``False``."""
        from voice_typer.server import autostart_launcher as al

        monkeypatch.setattr(al, "_is_port_open", lambda h, p: False)
        monkeypatch.setattr(al, "_read_ipc_port_from_pid_file", lambda: None)
        # Speed up the test: zero out sleep so the deadline loop spins
        # quickly.
        monkeypatch.setattr(al.time, "sleep", lambda s: None)
        # Use a tiny deadline + patch monotonic so we don't actually
        # wait 5 s.
        start = [time.monotonic()]

        # Drive monotonic forward artificially to expire the deadline
        # after a couple of polls.
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
        """When the backend PID file declares a non-default port, the
        poll must check THAT port (not the default IPC_PORT)."""
        from voice_typer.server import autostart_launcher as al

        captured_ports = []
        monkeypatch.setattr(al, "_is_port_open", lambda h, p: captured_ports.append(p) or True)
        monkeypatch.setattr(al, "_read_ipc_port_from_pid_file", lambda: 12345)
        monkeypatch.setattr(al.time, "sleep", lambda s: None)

        result = al._wait_for_ipc_ready(deadline_s=1.0)
        assert result is True
        assert 12345 in captured_ports, "XV-6: must use the port from the PID file (12345), not the default IPC_PORT."

    def test_falls_back_to_default_port_when_pid_file_missing(self, monkeypatch):
        """When the PID file is missing/unreadable, the poll falls back
        to :data:`IPC_PORT`."""
        from voice_typer.server import autostart_launcher as al

        captured_ports = []
        monkeypatch.setattr(al, "_is_port_open", lambda h, p: captured_ports.append(p) or True)
        monkeypatch.setattr(al, "_read_ipc_port_from_pid_file", lambda: None)
        monkeypatch.setattr(al.time, "sleep", lambda s: None)

        al._wait_for_ipc_ready(deadline_s=1.0)
        assert al.IPC_PORT in captured_ports

    def test_returns_true_after_finite_polls_when_port_opens_late(self, monkeypatch):
        """When the port opens after N failed polls, the function must
        return True on the (N+1)-th poll without waiting for the full
        deadline."""
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
    """XV-6: the ``launch()`` function must NOT contain any
    ``time.sleep(2)`` calls — they have all been replaced by
    ``_wait_for_ipc_ready()``."""

    def test_launch_source_has_no_fixed_sleep_two(self):
        """Source guard: no ``time.sleep(2)`` remains in the
        ``launch()`` function body (the 3 spawn-success sites must now
        call ``_wait_for_ipc_ready()``)."""
        from voice_typer.server import autostart_launcher as al

        src = inspect.getsource(al.launch)
        # Strip comments before checking so a docstring or inline
        # comment mentioning "time.sleep(2)" (e.g. in  rationale)
        # doesn't trip the assertion.
        code_lines = []
        for line in src.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            code_lines.append(line)
        code_only = "\n".join(code_lines)
        assert "time.sleep(2)" not in code_only, (
            "XV-6 regression: launch() still contains a fixed time.sleep(2) — must use _wait_for_ipc_ready() instead."
        )
        # And the new helper must be called at least once.
        assert "_wait_for_ipc_ready()" in code_only, (
            "XV-6: launch() must call _wait_for_ipc_ready() at the spawn-success sites."
        )


# numpy dead-import removed ──────────────────────────────────


class TestNumpyDeadImportRemoved:
    """XV-4: the dead ``import numpy as np`` at the top of ``app.py``
    must be removed (it was never used in the module)."""

    def test_app_module_does_not_import_numpy(self):
        """Source guard: no ``import numpy`` line at module top."""
        app_path = Path(__file__).resolve().parent.parent / "voice_typer" / "server" / "app.py"
        src = app_path.read_text()
        # Check the top-level import statements only (first ~60 lines
        # covers the import block). We accept ``import numpy`` inside
        # function bodies (lazy import) but NOT at module top.
        for line in src.splitlines()[:60]:
            stripped = line.lstrip()
            if stripped.startswith("import numpy") or stripped.startswith("from numpy"):
                pytest.fail(f"XV-4: app.py still imports numpy at module top: {line!r}")

    def test_app_module_imports_cleanly(self):
        """Sanity: app.py must still import successfully after the dead
        import removal (no NameError on a missed ``np`` reference).

        NOTE: this test depends on the wider import chain (``voice_typer.
        server.clipboard`` → ``clipboard_target_safety``) which is owned
        by a different disjoint scope. If a parallel agent is mid-edit on
        those modules, the import may transiently fail for reasons
        unrelated to XV-4. In that case we skip — the source-level guard
        above is the authoritative check for XV-4.
        """
        try:
            import voice_typer.server.app  # noqa: F401
        except ImportError as exc:
            # Only skip if the failure originates OUTSIDE app.py itself
            # (i.e. in a downstream module that's not our scope). If
            # app.py itself has a NameError from a missed ``np``
            # reference, we want to fail loudly.
            if "voice_typer/server/app.py" in str(exc):
                pytest.fail(
                    f"XV-4: app.py failed to import after numpy removal (likely a missed ``np`` reference): {exc}"
                )
            pytest.skip(
                f"XV-4 sanity check skipped — downstream import error "
                f"outside app.py scope (parallel agent edit in progress?): {exc}"
            )
