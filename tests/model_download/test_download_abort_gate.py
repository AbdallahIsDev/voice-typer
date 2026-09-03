"""True pause/abort for model downloads (transfer-gate contract).

Pre-fix, ``pause_model_download`` only froze the progress REPORTER: the
daemon HuggingFace transfer thread kept downloading to completion while
the UI showed "paused" (the user saw a green bar that kept progressing).
The fix intercepts the transfer at every ~10 MB chunk boundary via a
tqdm subclass installed through ``snapshot_download(tqdm_class=...)``:

- pause  → the gate BLOCKS the transfer thread (bytes stop),
- cancel → the gate raises :class:`ModelDownloadAborted`, which
  unwinds the transfer instead of finishing in the background.

These tests pin the gate semantics, the abort lifecycle, the retry
wrapper's no-retry-on-abort behavior, and the HTTP-path forcing.
"""

import threading

import pytest
from voice_typer.server.asr_setup import (
    ModelDownloadAborted,
    clear_download_pause_state,
    force_http_download_path,
    get_download_tqdm_class,
    is_download_paused,
    request_download_abort,
    reset_download_pause_state,
    set_download_paused,
)


@pytest.fixture(autouse=True)
def _fresh_download_state():
    """Isolate the module-level pause/abort events per test."""
    reset_download_pause_state()
    yield
    clear_download_pause_state()


@pytest.fixture()
def gate():
    """A real gate instance with tqdm rendering disabled."""
    cls = get_download_tqdm_class()
    return cls(disable=True)


class TestAbortLifecycle:
    def test_request_abort_returns_true_with_active_download(self):
        reset_download_pause_state()
        assert request_download_abort() is True

    def test_request_abort_returns_false_after_clear(self):
        clear_download_pause_state()
        assert request_download_abort() is False

    def test_reset_clears_a_stale_abort(self):
        request_download_abort()
        reset_download_pause_state()
        assert request_download_abort() is True  # fresh event, abortable


class TestGateBlocking:
    """The gate must BLOCK while paused — this is what stops the actual
    transfer bytes (the pre-fix bug: only the reporting froze)."""

    def test_gate_check_blocks_while_paused_then_returns_on_resume(self, gate):
        entered = threading.Event()
        result: dict = {}

        def worker():
            entered.set()
            gate._gate_check()  # must not return while paused
            result["resumed"] = True

        set_download_paused(True)
        t = threading.Thread(target=worker, daemon=True)
        t.start()
        assert entered.wait(timeout=2)
        # Still blocked: the gate must NOT return while the download is paused.
        t.join(timeout=0.5)
        assert t.is_alive(), "gate returned while the download was paused"
        assert "resumed" not in result

        set_download_paused(False)
        t.join(timeout=2)
        assert not t.is_alive()
        assert result.get("resumed") is True

    def test_gate_check_raises_abort_when_cancelled(self, gate):
        request_download_abort()
        with pytest.raises(ModelDownloadAborted):
            gate._gate_check()

    def test_gate_check_raises_abort_while_paused(self, gate):
        """Cancel during a pause must wake the blocked transfer and
        unwind it — a paused-then-cancelled download must not linger."""
        set_download_paused(True)
        request_download_abort()
        with pytest.raises(ModelDownloadAborted):
            gate._gate_check()

    def test_update_passes_through_when_active(self, gate):
        """No pause + no abort → update() reaches the real tqdm (disabled)
        without raising, i.e. a healthy download is untouched."""
        gate.update(10)  # must not raise


class TestRetryWrapperDoesNotRetryAbort:
    def test_download_with_retry_propagates_abort_without_retry(self):
        """ModelDownloadAborted is a BaseException precisely so the
        retry wrapper's ``except Exception`` cannot swallow it — a
        cancelled download must never resume downloading via retry."""
        from voice_typer.server.asr_utils import _download_with_retry

        attempts: list = []

        def fake_download(**kwargs):
            attempts.append(1)
            raise ModelDownloadAborted("aborted")

        with pytest.raises(ModelDownloadAborted):
            _download_with_retry(fake_download, max_attempts=3, delays=(0.0, 0.0))
        assert len(attempts) == 1, "abort was retried — a cancel must not resume downloading"

    def test_download_with_retry_still_retries_plain_errors(self):
        from voice_typer.server.asr_utils import _download_with_retry

        attempts: list = []

        def fake_download(**kwargs):
            attempts.append(1)
            raise RuntimeError("transient network error")

        with pytest.raises(RuntimeError):
            _download_with_retry(fake_download, max_attempts=3, delays=(0.0, 0.0))
        assert len(attempts) == 3


class TestForcedHttpPath:
    def test_force_http_download_path_disables_xet(self):
        """The pause/abort gate lives in the HTTP chunk loop's progress
        callbacks; the xet path reports from native threads where a
        callback cannot stop the transfer. The download path must run
        with xet disabled regardless of import order."""
        import os

        import huggingface_hub.constants as hf_constants

        old_env = os.environ.get("HF_HUB_DISABLE_XET")
        old_flag = hf_constants.HF_HUB_DISABLE_XET
        try:
            os.environ.pop("HF_HUB_DISABLE_XET", None)
            hf_constants.HF_HUB_DISABLE_XET = False
            force_http_download_path()
            assert os.environ["HF_HUB_DISABLE_XET"] == "true"
            assert hf_constants.HF_HUB_DISABLE_XET is True
        finally:
            if old_env is None:
                os.environ.pop("HF_HUB_DISABLE_XET", None)
            else:
                os.environ["HF_HUB_DISABLE_XET"] = old_env
            hf_constants.HF_HUB_DISABLE_XET = old_flag


class TestPauseFlagSemanticsUnchanged:
    def test_pause_flag_contract(self):
        """The polling loop's pause event contract is untouched: the gate
        ADDS transfer-thread blocking on top of it."""
        assert is_download_paused() is False
        assert set_download_paused(True) is True
        assert is_download_paused() is True
        assert set_download_paused(False) is True
        assert is_download_paused() is False

    def test_clear_then_set_returns_false(self):
        clear_download_pause_state()
        assert set_download_paused(True) is False
        assert is_download_paused() is False


class TestGateWiredIntoDownloadPaths:
    """Source-level pins: both HuggingFace download paths must install
    the gate (a future refactor dropping ``tqdm_class=`` would silently
    resurrect the pause-only-freezes-reporting bug)."""

    def test_whisper_branch_passes_gate(self):
        import inspect

        from voice_typer.server.service.model import _downloads

        src = inspect.getsource(_downloads.DownloadsMixin._download_whisper_family)
        assert "tqdm_class=get_download_tqdm_class()" in src
        assert "force_http_download_path()" in src

    def test_parakeet_path_passes_gate(self):
        import inspect

        import voice_typer.server.asr_setup as asr

        src = inspect.getsource(asr.download_parakeet_weights)
        assert "tqdm_class=get_download_tqdm_class()" in src
        assert "force_http_download_path()" in src

    def test_cancel_signals_abort(self):
        import inspect

        from voice_typer.server.service.model import _downloads

        src = inspect.getsource(_downloads.DownloadsMixin.cancel_model_download)
        assert "request_download_abort()" in src


def _make_service(tmp_config_dir):
    """Minimal VoiceTyperService (same pattern as test_service_fixes)."""
    from unittest.mock import MagicMock

    from voice_typer.server import service as svc_mod

    class FakeApp:
        config = type("FakeConfig", (), {})()
        _microphones: list = []
        tray = MagicMock()

    return svc_mod.VoiceTyperService(FakeApp())


class TestSingleFlightGuard:
    """A second download_model IPC while one is active (possibly paused)
    must be REFUSED, never started: the shared pause/abort events are
    module-level, and recycling them under a live download would wake the
    parked gate and run two concurrent transfers."""

    def test_whisper_branch_refuses_second_download(self, tmp_config_dir, monkeypatch):
        from unittest.mock import MagicMock

        import voice_typer.server.asr_setup as asr

        asr.reset_download_pause_state()  # simulate a download in flight
        try:
            monkeypatch.setattr(
                "huggingface_hub.snapshot_download",
                lambda *a, **k: (_ for _ in ()).throw(AssertionError("second download must not reach HuggingFace")),
            )
            svc = _make_service(tmp_config_dir)
            meta = MagicMock()
            meta.repo_id = "org/target"
            meta.backend = "whisper"
            meta.download_size_mb = 1
            outcome = svc._download_whisper_family("tiny", meta)
            assert outcome.get("download_already_active") is True
            assert outcome["success"] is False
            assert outcome["model"] == "tiny"
        finally:
            asr.clear_download_pause_state()

    def test_parakeet_branch_refuses_second_download(self, tmp_config_dir, monkeypatch):
        import voice_typer.server.asr_setup as asr

        asr.reset_download_pause_state()
        try:
            monkeypatch.setattr(
                "voice_typer.server.asr_setup.download_parakeet_weights",
                lambda *a, **k: (_ for _ in ()).throw(
                    AssertionError("second download must not reach download_parakeet_weights")
                ),
            )
            svc = _make_service(tmp_config_dir)
            monkeypatch.setattr(svc, "_require_huggingface_consent", lambda name: None)
            outcome = svc._download_parakeet("parakeet")
            assert outcome.get("download_already_active") is True
            assert outcome["success"] is False
            assert outcome["model"] == "parakeet"
        finally:
            asr.clear_download_pause_state()

    def test_retry_allowed_after_download_ends(self, tmp_config_dir, monkeypatch):
        """Once the active download exits (events cleared), a new download
        must be accepted — the guard is a single-flight latch, not a
        permanent lock."""

        import voice_typer.server.asr_setup as asr

        asr.clear_download_pause_state()
        svc = _make_service(tmp_config_dir)
        monkeypatch.setattr(svc, "_require_huggingface_consent", lambda name: None)
        monkeypatch.setattr(
            "voice_typer.server.asr_setup.download_parakeet_weights",
            lambda *a, **k: (True, "", None),
        )
        outcome = svc._download_parakeet("parakeet")
        assert outcome["success"] is True
        assert "download_already_active" not in outcome


class TestParakeetGateLifecycle:
    """The Parakeet path must ARM the gate's pause/abort events for the
    duration of its download (the whisper branch does its own reset) —
    with the events unset the gate treats ``None`` as "no active
    download" and aborts at the first chunk boundary."""

    def test_events_active_during_and_cleared_after(self, tmp_config_dir, monkeypatch):
        import voice_typer.server.asr_setup as asr

        asr.clear_download_pause_state()
        observed: dict = {}

        def fake_dpw(*args, **kwargs):
            observed["active_during"] = asr.is_download_active()
            observed["abortable_during"] = asr.request_download_abort()
            return (True, "", None)

        monkeypatch.setattr(asr, "download_parakeet_weights", fake_dpw)
        svc = _make_service(tmp_config_dir)
        monkeypatch.setattr(svc, "_require_huggingface_consent", lambda name: None)

        outcome = svc._download_parakeet("parakeet")
        assert outcome["success"] is True
        assert observed["active_during"] is True, "pause/abort events must be ARMED while the parakeet transfer runs"
        assert observed["abortable_during"] is True, "cancel must be able to abort the parakeet transfer"
        assert asr.is_download_active() is False, "events must be cleared after the download exits"

    def test_abort_unwinds_parakeet_to_cancelled_outcome(self, tmp_config_dir, monkeypatch):
        import voice_typer.server.asr_setup as asr

        asr.clear_download_pause_state()

        def fake_dpw(*args, **kwargs):
            raise asr.ModelDownloadAborted("cancelled mid-transfer")

        monkeypatch.setattr(asr, "download_parakeet_weights", fake_dpw)
        svc = _make_service(tmp_config_dir)
        monkeypatch.setattr(svc, "_require_huggingface_consent", lambda name: None)

        # The ModelDownloadAborted → cancelled mapping lives in the
        # download_model dispatcher (the branch method lets the abort
        # unwind through its finally), so drive the dispatcher.
        outcome = svc.download_model("parakeet")
        assert outcome["cancelled"] is True
        assert outcome["success"] is False
        assert outcome["model"] == "parakeet"
        assert asr.is_download_active() is False


class TestCancelAbortsGateWithoutRegistryEvent:
    def test_cancel_during_pause_aborts_transfer(self, tmp_config_dir):
        """Cancel must work from a PAUSED state even when no per-download
        cancel Event is registered (the Parakeet path) — the parked gate
        wakes and unwinds."""
        import pytest as _pytest
        import voice_typer.server.asr_setup as asr

        asr.reset_download_pause_state()
        asr.set_download_paused(True)
        svc = _make_service(tmp_config_dir)
        result = svc.cancel_model_download()
        assert result["cancelled"] is True
        gate_cls = asr.get_download_tqdm_class()
        gate = gate_cls(disable=True)
        with _pytest.raises(asr.ModelDownloadAborted):
            gate._gate_check()


class TestSnapshotCompletenessProbe:
    """``is_model_snapshot_complete`` — the honest 'downloaded' answer.

    The old checks treated a bare ``models--<repo>`` directory (created
    at download START) as downloaded, so a paused / killed download
    showed a usable model in the UI."""

    def test_missing_repo_dir_is_false_without_hf_call(self, tmp_config_dir, monkeypatch):
        import voice_typer.server.transcription_download as td

        monkeypatch.setattr(
            "huggingface_hub.snapshot_download",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("probe must not call hf when the repo dir is absent")),
        )
        assert td.is_model_snapshot_complete("org/never-downloaded") is False

    def test_complete_snapshot_true(self, tmp_config_dir, monkeypatch):
        import voice_typer.server.transcription_download as td
        from voice_typer.server.config import _config_dir

        # The probe short-circuits when the repo dir is absent — create
        # the on-disk marker a real download START would leave behind.
        repo_dir = _config_dir() / "huggingface" / "hub" / "models--Systran--faster-whisper-tiny"
        repo_dir.mkdir(parents=True)

        calls: list = []

        def fake_snapshot(**kwargs):
            calls.append(kwargs)
            return "/cache/path"

        monkeypatch.setattr("huggingface_hub.snapshot_download", fake_snapshot)
        assert td.is_model_snapshot_complete("Systran/faster-whisper-tiny") is True
        assert calls[0]["local_files_only"] is True
        assert calls[0]["repo_id"] == "Systran/faster-whisper-tiny"

    def test_partial_snapshot_false(self, tmp_config_dir, monkeypatch):
        """An incomplete snapshot raises inside the local-only probe —
        that exception MUST map to False (not crash the status poll)."""
        import voice_typer.server.transcription_download as td

        def fake_snapshot(**kwargs):
            raise RuntimeError("LocalEntryNotFoundError: incomplete snapshot")

        monkeypatch.setattr("huggingface_hub.snapshot_download", fake_snapshot)
        assert td.is_model_snapshot_complete("Systran/faster-whisper-tiny") is False

    def test_parakeet_repo_uses_onnx_patterns(self, tmp_config_dir, monkeypatch):
        import voice_typer.server.transcription_download as td
        from voice_typer.server.config import _config_dir

        repo_dir = _config_dir() / "huggingface" / "hub" / "models--grikdotnet--parakeet-tdt-0.6b-fp16"
        repo_dir.mkdir(parents=True)

        captured: list = []

        def fake_snapshot(**kwargs):
            captured.append(kwargs)
            return "/cache/path"

        monkeypatch.setattr("huggingface_hub.snapshot_download", fake_snapshot)
        assert td.is_model_snapshot_complete("grikdotnet/parakeet-tdt-0.6b-fp16") is True
        assert "*.onnx" in str(captured[0]["allow_patterns"])
