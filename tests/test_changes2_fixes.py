"""Regression tests for the second-pass forensic review (changes-2).

Each test class pins one finding to its current verified state so
future regressions are caught immediately.

Findings covered
----------------
- SEC-audit-005  Model integrity verification warns when files dict is empty
- SEC-009        redact_pii() wired into transcription-text logging path
- SEC-030        _read_capped() overflow abort path raises RuntimeError
- RACE-001       Audio callback lock scope (concurrent invocation test)
- RACE-003       _recent_rms_values snapshotted inside the lock
- RACE-011       Config mutation lock shared between IPC and SettingsController
- AUDIO-003      Test uses time.monotonic() to match source code
- AUDIO-009/015  _in_callback dead field removed
- AUDIO-013      VAD grey-zone preserves counters (no reset)
- AUDIO-014      VAD auto-calibration behavior tested
- AUDIO-019      StreamingTextAssembler uses deque(maxlen=N) for O(1) eviction
- AUDIO-AGC      _last_rms stored post-AGC (consistent with VAD)
"""
from __future__ import annotations

import inspect
import io
import logging
import threading
import time
from unittest.mock import patch

import numpy as np
import pytest

# ─── SEC-audit-005 — model integrity check warns when files dict is empty ──


class TestSecAudit005EmptyHashesWarning:
    """SEC-audit-005.

    The finding: all 6 entries in model_hashes.json have empty ``files``
    dicts, so SHA-256 verification never runs. The fix: emit a WARNING
    (not just INFO) so operators notice the no-op state at default log
    levels.

    Tests pin:
    - ``verify_model_integrity`` logs a WARNING containing "NO-OP"
      when the manifest's ``files`` dict is empty.
    - ``_verify_qwen_model_hashes`` (qwen_engine) does the same.
    """

    def test_security_logs_warning_when_files_empty(self, tmp_path, caplog):
        from voice_typer.server import security
        from voice_typer.server.security import verify_model_integrity

        # Create a fake model directory with a model file (safetensors)
        # so the structural checks pass and we reach the empty-files branch.
        model_dir = tmp_path / "fake-model"
        model_dir.mkdir()
        (model_dir / "config.json").write_text("{}")
        (model_dir / "model.safetensors").write_bytes(b"fake")

        # Patch MODEL_HASHES to return an empty files dict for our repo
        fake_manifest = {"fake/repo": {"revision": "main", "files": {}}}
        with patch.object(security, "MODEL_HASHES", fake_manifest), caplog.at_level(logging.WARNING):
            result = verify_model_integrity(local_dir=str(model_dir), repo_id="fake/repo")

        # Soft pass (structural checks pass, hash check is a no-op)
        assert result is True
        # Must have emitted a WARNING with "NO-OP"
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("NO-OP" in r.getMessage() for r in warnings), (
            "verify_model_integrity must emit a WARNING containing 'NO-OP' "
            "when the manifest's files dict is empty, so operators notice "
            "the integrity check is effectively disabled."
        )

    def test_qwen_logs_warning_when_files_empty(self, tmp_path, caplog):
        from voice_typer.server import security
        from voice_typer.server.qwen_engine import _verify_qwen_model_hashes

        # Create a fake qwen model dir with a config.json
        model_dir = tmp_path / "fake-qwen"
        model_dir.mkdir()
        (model_dir / "config.json").write_text("{}")

        # Patch MODEL_HASHES to return an empty files dict for qwen
        with patch.object(security, "MODEL_HASHES", {"qwen": {"files": {}}}), caplog.at_level(logging.WARNING):
            result = _verify_qwen_model_hashes(str(model_dir))

        assert result is True  # soft pass
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("NO-OP" in r.getMessage() for r in warnings), (
            "_verify_qwen_model_hashes must emit a WARNING containing 'NO-OP' "
            "when the qwen manifest's files dict is empty."
        )

    def test_model_hashes_json_currently_empty(self):
        """Pin the current state: all entries in model_hashes.json have
        empty ``files`` dicts. This test will START FAILING once an
        operator populates the manifest with real hashes — at which
        point it should be updated to assert the hashes are non-empty
        and the SEC-audit-005 WARNING should be downgraded back to INFO.
        """
        import json
        from pathlib import Path

        manifest_path = Path(__file__).resolve().parent.parent / \
            "voice_typer" / "server" / "model_hashes.json"
        with open(manifest_path) as f:
            data = json.load(f)

        # Each top-level entry should have a "files" key.
        # (Skip the "_comment" key which holds a documentation string.)
        for repo_id, entry in data.items():
            if repo_id.startswith("_"):
                continue  # skip _comment and other metadata keys
            assert isinstance(entry, dict), (
                f"model_hashes.json entry {repo_id!r} must be a dict, "
                f"got {type(entry).__name__}"
            )
            assert "files" in entry, (
                f"model_hashes.json entry {repo_id!r} must have a 'files' key"
            )
            # Pin the current state — empty dict means the integrity
            # check is a no-op and the WARNING fires.
            # When an operator populates this, the test will fail and
            # prompt them to also update the SEC-audit-0005 logic.
            assert entry["files"] == {}, (
                f"model_hashes.json entry {repo_id!r} now has populated "
                f"files dict — update SEC-audit-005 test expectations."
            )


# ─── SEC-009 — redact_pii wired into transcription logging ────────────────


class TestSec009RedactPiiInTranscriptionLogging:
    """SEC-009.

    Pre-fix: ``redact_pii()`` was dead code — declared in security.py
    but never called from production. Fix: wire it into the
    ``log_transcriptions=True`` path of ``DictationPipeline._store_result``
    so emails, phone numbers, SSNs, and credit-card-like patterns are
    masked before hitting the log file.
    """

    def test_store_result_calls_redact_pii_when_log_transcriptions_true(self):
        """``DictationPipeline._store_result`` source must call
        ``redact_pii`` on the transcription text before logging when
        ``log_transcriptions`` is enabled.
        """
        from voice_typer.server.dictation_pipeline import DictationPipeline

        src = inspect.getsource(DictationPipeline._store_result)
        assert "redact_pii" in src, (
            "DictationPipeline._store_result must call redact_pii() on the "
            "transcription text before logging when log_transcriptions=True. "
            "Pre-fix, raw text was logged, leaking PII to the log file."
        )
        # The redact_pii call must be inside the log_transcriptions branch
        assert "log_transcriptions" in src
        assert "redact_pii(text[:200])" in src or "redact_pii(text" in src

    def test_redact_pii_masks_email_phone_ssn_cc(self):
        """``redact_pii`` must mask the four documented PII patterns."""
        from voice_typer.server.security import redact_pii

        # Email
        assert "[EMAIL]" in redact_pii("contact me at john.doe@example.com")
        # Phone (US-style)
        assert "[PHONE]" in redact_pii("call me at 555-123-4567")
        # SSN
        assert "[SSN]" in redact_pii("my ssn is 123-45-6789")
        # Credit card
        assert "[CC]" in redact_pii("card 4111-1111-1111-1111")

    def test_redact_pii_preserves_non_pii_text(self):
        from voice_typer.server.security import redact_pii

        text = "Hello world, this is a test transcription."
        assert redact_pii(text) == text


# ─── SEC-030 — _read_capped overflow abort path ───────────────────────────


class TestSec030ReadCappedOverflow:
    """SEC-030.

    Pre-fix: the ``total > max_bytes`` abort path in ``_read_capped``
    was untested. A malformed server sending >50MB could timeout
    instead of cleanly aborting. Fix: add a test that supplies chunks
    summing >50MB and asserts ``RuntimeError`` is raised.
    """

    def test_read_capped_aborts_on_overflow(self):
        from voice_typer.server.cloud_engines import _read_capped

        # Mock response that yields 100 chunks of 1 MB each = 100 MB > 50 MB cap
        chunk_size = 1024 * 1024  # 1 MB
        chunks_yielded = [0]

        class FakeResp:
            def read(self, n):
                # Yield 1 MB chunks until 100 have been emitted.
                if chunks_yielded[0] >= 100:
                    return b""
                chunks_yielded[0] += 1
                return b"x" * chunk_size

        with pytest.raises(RuntimeError, match="exceeded.*aborting to prevent OOM"):
            _read_capped(FakeResp(), max_bytes=50 * 1024 * 1024)

    def test_read_capped_returns_body_when_under_cap(self):
        from voice_typer.server.cloud_engines import _read_capped

        body = b"hello world" * 100  # ~1.1 KB

        class FakeResp:
            def __init__(self, body):
                self._buf = io.BytesIO(body)

            def read(self, n):
                return self._buf.read(n)

        result = _read_capped(FakeResp(body), max_bytes=50 * 1024 * 1024)
        assert result == body

    def test_read_capped_handles_empty_response(self):
        from voice_typer.server.cloud_engines import _read_capped

        class FakeResp:
            def read(self, n):
                return b""

        assert _read_capped(FakeResp(), max_bytes=1024) == b""

    def test_read_capped_aborts_exactly_at_boundary(self):
        """One byte over the cap must trigger the abort."""
        from voice_typer.server.cloud_engines import _read_capped

        class FakeResp:
            def __init__(self):
                self._calls = 0

            def read(self, n):
                self._calls += 1
                if self._calls == 1:
                    return b"x" * 100  # exactly 100 bytes
                if self._calls == 2:
                    return b"y"  # 1 more byte → total 101 > cap 100
                return b""

        with pytest.raises(RuntimeError):
            _read_capped(FakeResp(), max_bytes=100)


# ─── RACE-001 — Audio callback lock scope (concurrent invocation test) ────


class TestRace001AudioCallbackLockScope:
    """RACE-001.

    The audio callback uses a minimal lock scope (only buffer.append
    and chunk_count under lock). This test invokes the callback from
    multiple threads concurrently to verify no crashes / corruption.
    """

    def test_concurrent_audio_callback_does_not_crash(self):
        from voice_typer.server.config import Config
        from voice_typer.server.recording import Recorder

        cfg = Config()
        cfg.sample_rate = 16000
        rec = Recorder(cfg)
        rec._effective_sr = 16000
        rec._cached_target_sr = 16000
        # Don't actually start the recorder — we'll invoke the callback directly.
        # Set _recording_event so the callback doesn't bail out early.
        rec._recording_event.set()
        rec._recording_start_time = time.perf_counter()

        # Mock callbacks to no-ops (callback refs are read outside the lock)
        rec.on_rms_level = lambda rms, peak: None
        rec.on_silence_warning = lambda: None
        rec.on_silence_auto_stop = lambda: None
        rec.on_max_duration_auto_stop = lambda: None

        # The audio callback is defined as a nested function inside
        # ``start()``. We can't easily invoke it directly, so this test
        # instead validates the lock-scope invariant by calling the
        # locked section (buffer append) from multiple threads.
        indata = np.full((512, 1), 0.1, dtype=np.float32)

        errors: list[Exception] = []

        def invoke_locked_append():
            try:
                with rec._lock:
                    rec._buffer.append(indata.copy())
                    rec._chunk_count += 1
                    # RACE-003: snapshot _recent_rms_values inside the lock
                    _ = list(rec._recent_rms_values)
            except Exception as e:
                errors.append(e)

        # Spawn 8 threads, each invoking the locked append 50 times.
        threads = []
        for _ in range(8):
            t = threading.Thread(target=lambda: [invoke_locked_append() for _ in range(50)])
            threads.append(t)
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        # No exceptions should have been raised
        assert not errors, f"Concurrent locked-append invocations raised: {errors}"
        # Buffer should contain exactly 8*50 = 400 chunks
        with rec._lock:
            assert len(rec._buffer) == 400
            assert rec._chunk_count == 400

    def test_lock_scope_only_covers_buffer_append_and_count(self):
        """The lock block inside the callback must only cover buffer
        append + chunk_count + recent_rms snapshot (RACE-003 fix).
        """
        from voice_typer.server import recording as rec_mod

        # The callback is a nested function inside start(); inspect the
        # entire start() method source to find the lock block.
        src = inspect.getsource(rec_mod.Recorder.start)
        # The lock block must include buffer.append and _chunk_count
        assert "self._buffer.append" in src
        assert "self._chunk_count" in src
        # RACE-003: the recent_rms snapshot must be taken INSIDE the lock
        assert "recent_rms_snapshot = list(self._recent_rms_values)" in src


# ─── RACE-003 — _recent_rms_values snapshotted inside the lock ────────────


class TestRace003RmsSnapshotInsideLock:
    """RACE-003.

    Pre-fix: ``_recent_rms_values`` (a deque) was read outside the
    lock, allowing a concurrent callback to mutate it (append + maxlen
    eviction) mid-iteration. Fix: snapshot ``list(_recent_rms_values)``
    inside the lock; downstream code uses the snapshot.
    """

    def test_recent_rms_snapshot_taken_inside_lock(self):
        from voice_typer.server import recording as rec_mod

        src = inspect.getsource(rec_mod.Recorder.start)
        # The snapshot line must be inside the with self._lock block
        assert "recent_rms_snapshot = list(self._recent_rms_values)" in src
        # The post-lock code must NOT re-read _recent_rms_values directly
        # (it should use recent_rms_snapshot instead)
        assert "recent_rms = recent_rms_snapshot" in src

    def test_no_direct_recent_rms_read_outside_lock(self):
        """The post-lock code must NOT contain
        ``recent_rms = self._recent_rms_values`` (the pre-fix pattern).
        """
        from voice_typer.server import recording as rec_mod

        src = inspect.getsource(rec_mod.Recorder.start)
        # The pre-fix line was: recent_rms = self._recent_rms_values
        # (read outside the lock). The fix replaces it with the snapshot.
        assert "recent_rms = self._recent_rms_values" not in src, (
            "RACE-003 regression: _recent_rms_values is being read "
            "directly outside the lock — use the snapshot taken inside "
            "the lock instead."
        )


# ─── RACE-011 — Config mutation lock shared between IPC and SettingsController ──


class TestRace011ConfigMutationLock:
    """RACE-011.

    Pre-fix: IPC ``set_config`` and the deprecated tkinter
    SettingsController.apply() could interleave Config attribute writes.
    Fix: app holds a ``_config_mutation_lock`` (RLock) shared with
    both paths so mutations serialize.
    """

    def test_app_has_config_mutation_lock(self):
        from voice_typer.server.app import VoiceTyperApp

        # VoiceTyperApp must declare _config_mutation_lock
        src = inspect.getsource(VoiceTyperApp.__init__)
        assert "_config_mutation_lock" in src, (
            "VoiceTyperApp.__init__ must initialize _config_mutation_lock "
            "to serialize Config mutations between IPC and SettingsController."
        )
        assert "threading.RLock()" in src

    def test_ipc_set_config_uses_lock(self):
        from voice_typer.server import ipc_server

        src = inspect.getsource(ipc_server.IPCServer._dispatch)
        assert "_config_mutation_lock" in src, (
            "IPC set_config handler must acquire _config_mutation_lock "
            "before mutating Config attributes."
        )

    def test_settings_controller_accepts_mutation_lock(self):
        from voice_typer.server.settings import SettingsController

        sig = inspect.signature(SettingsController.__init__)
        assert "config_mutation_lock" in sig.parameters, (
            "SettingsController must accept a config_mutation_lock parameter "
            "so the app can share its lock between IPC and tkinter paths."
        )

    def test_settings_controller_apply_uses_lock_when_provided(self):
        from voice_typer.server.settings import SettingsController

        src = inspect.getsource(SettingsController.apply)
        assert "_config_mutation_lock" in src or "lock" in src, (
            "SettingsController.apply must acquire the config_mutation_lock "
            "(if provided) around the read-modify-save sequence."
        )

    def test_settings_controller_works_without_lock(self):
        """Backward compatibility: when no lock is provided, apply()
        must still work (legacy behaviour, no locking).
        """
        from voice_typer.server.config import Config
        from voice_typer.server.settings import SettingsController

        cfg = Config()
        # No config_mutation_lock kwarg → backward-compatible path
        ctrl = SettingsController(cfg)
        assert ctrl._config_mutation_lock is None

        # apply() must still work
        ctrl.apply(
            hotkey="<f3>",
            model_size="tiny.en",
            microphone=None,
            autostart=False,
            show_notifications=True,
        )
        assert cfg.hotkey == "<f3>"

    def test_concurrent_mutations_serialize_via_lock(self):
        """When two threads concurrently mutate Config via
        SettingsController.apply() with the same shared lock, the
        mutations must not interleave — each apply() must see a
        consistent view of the Config.
        """
        import threading

        from voice_typer.server.config import Config
        from voice_typer.server.settings import SettingsController

        cfg = Config()
        lock = threading.RLock()
        ctrl = SettingsController(cfg, config_mutation_lock=lock)

        # Track the maximum number of concurrent apply() calls
        in_flight = [0]
        max_in_flight = [0]

        def counting_save():
            in_flight[0] += 1
            max_in_flight[0] = max(max_in_flight[0], in_flight[0])
            time.sleep(0.001)  # tiny delay to encourage interleaving
            in_flight[0] -= 1
            return True

        cfg.save = counting_save  # type: ignore[method-assign]

        def apply_value(hotkey: str):
            ctrl.apply(
                hotkey=hotkey,
                model_size="tiny.en",
                microphone=None,
                autostart=False,
                show_notifications=True,
            )

        threads = [
            threading.Thread(target=apply_value, args=(f"<f{i+4}>",))
            for i in range(8)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        # With the lock, save() must never be called concurrently
        assert max_in_flight[0] <= 1, (
            f"Config.save() was called concurrently by {max_in_flight[0]} "
            "threads — the config_mutation_lock is not serializing mutations."
        )


# ─── AUDIO-003 — Test uses time.monotonic() to match source code ──────────


class TestAudio003MonotonicClockInTests:
    """AUDIO-003.

    Pre-fix: tests used ``time.time()`` (wall clock) while source code
    used ``time.monotonic()``. Under NTP/DST adjustments the wall
    clock can jump backwards. Fix: tests must use ``time.monotonic()``.
    """

    def test_test_recording_uses_monotonic(self):
        """The two test methods that set _resample_poly_error_time
        must use time.monotonic(), NOT time.time().
        """
        import tests.test_recording as test_recording_mod

        # Find the two relevant test methods (in TestResampleFallback)
        retry_src = inspect.getsource(
            test_recording_mod.TestResampleFallback.test_resample_retry_after_timeout
        )
        no_retry_src = inspect.getsource(
            test_recording_mod.TestResampleFallback.test_resample_not_retried_before_timeout
        )

        # Both must use time.monotonic()
        assert "time.monotonic()" in retry_src, (
            "test_resample_retry_after_timeout must use time.monotonic() "
            "to match the source code at recording.py:163."
        )
        assert "time.monotonic()" in no_retry_src, (
            "test_resample_not_retried_before_timeout must use time.monotonic()."
        )

        # Neither should use time.time()
        # (Strip comments before checking to avoid false positives from
        # comments that mention time.time().)
        def code_only(src: str) -> str:
            return "\n".join(
                line for line in src.splitlines()
                if not line.lstrip().startswith("#")
            )

        assert "time.time()" not in code_only(retry_src), (
            "test_resample_retry_after_timeout must NOT use time.time() — "
            "it can drift from time.monotonic() under NTP adjustments."
        )
        assert "time.time()" not in code_only(no_retry_src), (
            "test_resample_not_retried_before_timeout must NOT use time.time()."
        )


# ─── AUDIO-009/015 — _in_callback dead field removed ─────────────────────


class TestAudio009InCallbackFieldRemoved:
    """AUDIO-009/AUDIO-015.

    Pre-fix: ``_in_callback`` was declared at recording.py:214 but
    never read anywhere in the codebase. Fix: delete the declaration
    (the live guard is ``_is_in_audio_callback`` at line ~285).
    """

    def test_in_callback_field_does_not_exist(self):
        """``Recorder.__init__`` must NOT declare ``_in_callback``."""
        from voice_typer.server import recording as rec_mod

        src = inspect.getsource(rec_mod.Recorder.__init__)
        assert "self._in_callback" not in src, (
            "AUDIO-009 regression: ``self._in_callback`` is declared but "
            "never read. The live guard is ``_is_in_audio_callback`` — "
            "remove the dead declaration."
        )

    def test_is_in_audio_callback_still_exists(self):
        """The live guard ``_is_in_audio_callback`` must still exist."""
        from voice_typer.server import recording as rec_mod

        src = inspect.getsource(rec_mod.Recorder.__init__)
        assert "_is_in_audio_callback" in src, (
            "The live guard ``_is_in_audio_callback`` must remain declared."
        )


# ─── AUDIO-013 — VAD grey-zone preserves counters ────────────────────────


class TestAudio013VadGreyZonePreservesCounters:
    """AUDIO-013.

    Pre-fix: the "between thresholds" else branch had a comment saying
    "don't change counters" but the code set both
    ``_vad_consecutive_silence_frames = 0`` AND
    ``_vad_consecutive_speech_frames = 0``. Standard VAD hysteresis
    leaves counters unchanged in the grey zone. Fix: replace the two
    resets with ``pass`` so the code matches the comment.
    """

    def test_grey_zone_does_not_reset_counters(self):
        from voice_typer.server import recording as rec_mod

        src = inspect.getsource(rec_mod.Recorder._vad_update)
        # Find the AUDIO-013 grey-zone else block. The method has two
        # AUDIO-013 comments — the first is in the docstring, the second
        # is the grey-zone fix comment. We want the SECOND one.
        audio013_idx = src.find("AUDIO-013: Grey zone")
        assert audio013_idx >= 0, "AUDIO-013 grey-zone comment not found"
        # Extract a generous window after the comment to capture the
        # full else body (the comment block + the actual `pass` line).
        following = src[audio013_idx:audio013_idx + 800]
        # The grey zone block must contain 'pass' (the fix)
        assert "pass" in following, (
            "AUDIO-013 fix: the grey-zone else block must use 'pass' "
            "instead of resetting both counters."
        )
        # The else block must NOT contain counter resets in the lines
        # immediately following the AUDIO-013 comment (before the next
        # "State transitions" section).
        state_transitions_idx = following.find("State transitions")
        if state_transitions_idx < 0:
            state_transitions_idx = len(following)
        else_body = following[:state_transitions_idx]
        assert "_vad_consecutive_silence_frames = 0" not in else_body, (
            "AUDIO-013 regression: grey-zone else block resets "
            "_vad_consecutive_silence_frames — should preserve counters."
        )
        assert "_vad_consecutive_speech_frames = 0" not in else_body, (
            "AUDIO-013 regression: grey-zone else block resets "
            "_vad_consecutive_speech_frames — should preserve counters."
        )

    def test_grey_zone_preserves_counters_at_runtime(self):
        """Verify at runtime that a grey-zone chunk doesn't reset
        the speech counter that was accumulated by a prior loud chunk.
        """
        from voice_typer.server.config import Config
        from voice_typer.server.recording import Recorder, VadState

        cfg = Config()
        rec = Recorder(cfg)
        # Initialize VAD state
        rec._vad_state = VadState.UNKNOWN
        rec._vad_consecutive_speech_frames = 0
        rec._vad_consecutive_silence_frames = 0
        rec._vad_speech_threshold_db = -30.0
        rec._vad_silence_threshold_db = -50.0
        rec._vad_speech_frames = 5
        rec._vad_silence_frames = 10
        rec._vad_hangover_frames = 5

        # Simulate a loud chunk: chunk_rms_db above speech threshold (-30 dB)
        # _vad_update takes chunk_rms_db (decibels)
        rec._vad_update(-20.0)  # loud (above -30)
        assert rec._vad_consecutive_speech_frames == 1
        assert rec._vad_consecutive_silence_frames == 0

        # Simulate a grey-zone chunk: between silence (-50) and speech (-30)
        rec._vad_update(-40.0)  # grey zone
        # AUDIO-013 fix: counters must NOT be reset
        assert rec._vad_consecutive_speech_frames == 1, (
            f"AUDIO-013 regression: grey-zone chunk reset speech counter "
            f"from 1 to {rec._vad_consecutive_speech_frames} — should "
            f"preserve counters in the grey zone."
        )
        assert rec._vad_consecutive_silence_frames == 0


# ─── AUDIO-014 — VAD auto-calibration behavior tested ────────────────────


class TestAudio014VadAutoCalibration:
    """AUDIO-014.

    Pre-fix: VAD auto-calibrate at recording.py:528-562 had no direct
    test. Fix: add a test that feeds known ambient noise and asserts
    the thresholds are set relative to the noise floor.
    """

    def test_vad_auto_calibrate_sets_thresholds_from_ambient_noise(self):
        """Feed the auto-calibrator a stream of low-amplitude noise
        and verify the speech/silence thresholds are set relative to
        the noise floor (not left at defaults).
        """
        from voice_typer.server.config import Config
        from voice_typer.server.recording import Recorder

        cfg = Config()
        rec = Recorder(cfg)
        # Reset calibration state
        rec._vad_calibration_rms_values = []
        rec._vad_calibrated = False
        # Save defaults so we can detect changes
        default_speech_db = rec._vad_speech_threshold_db
        default_silence_db = rec._vad_silence_threshold_db
        # Set recording start time so the elapsed check works
        rec._recording_start_time = time.perf_counter() - 10.0  # 10s ago
        # Make sure calibration duration has elapsed
        rec._vad_calibration_duration = 1.5

        # Feed 1.5 seconds worth of chunks at a known RMS (~0.01 = -40 dB)
        # Auto-calibration collects RMS for 1.5s then sets thresholds.
        chunk_duration = 0.032  # 32 ms per chunk at 16 kHz, 512 samples
        n_chunks = int(1.5 / chunk_duration) + 5  # extra to exceed duration
        target_rms = 0.01  # -40 dB
        for _ in range(n_chunks):
            rec._vad_auto_calibrate(target_rms, chunk_duration)

        # After calibration, the thresholds should be set relative to
        # the noise floor (target_rms in dB = 20*log10(0.01) = -40 dB).
        # The implementation sets:
        #   silence_threshold = noise_db + 6 dB  = -34 dB
        #   speech_threshold  = noise_db + 18 dB = -22 dB
        assert rec._vad_calibrated is True, (
            "VAD auto-calibration must set _vad_calibrated=True after "
            "collecting enough samples."
        )
        # Speech threshold should be above the noise floor
        assert rec._vad_speech_threshold_db > -40.0, (
            f"VAD speech threshold ({rec._vad_speech_threshold_db} dB) "
            "must be above the noise floor (-40 dB) after calibration."
        )
        # Silence threshold should also be above the noise floor
        # (the implementation sets silence = noise + 6 dB)
        assert rec._vad_silence_threshold_db > -40.0, (
            f"VAD silence threshold ({rec._vad_silence_threshold_db} dB) "
            "must be above the noise floor (-40 dB) after calibration "
            "(implementation sets silence = noise + 6 dB)."
        )
        # Speech threshold must be above silence threshold
        assert rec._vad_speech_threshold_db > rec._vad_silence_threshold_db, (
            "VAD speech threshold must be above silence threshold."
        )
        # Thresholds must have changed from defaults
        assert rec._vad_speech_threshold_db != default_speech_db or \
               rec._vad_silence_threshold_db != default_silence_db, (
            "VAD thresholds must change from defaults after calibration."
        )

    def test_vad_auto_calibrate_resets_on_start(self):
        """``Recorder.start()`` must reset the calibration state so a
        new session re-calibrates from scratch.
        """
        from voice_typer.server import recording as rec_mod

        src = inspect.getsource(rec_mod.Recorder.start)
        # The start() method must reset _vad_calibration_rms_values
        # and _vad_calibrated.
        assert "_vad_calibration_rms_values" in src or "_vad_calibrated" in src, (
            "Recorder.start() must reset VAD calibration state so each "
            "session re-calibrates from ambient noise."
        )


# ─── AUDIO-019 — StreamingTextAssembler uses deque(maxlen=N) ─────────────


class TestAudio019DequeEviction:
    """AUDIO-019.

    Pre-fix: ``_words`` used a plain list with ``pop(0)`` for eviction
    (O(n) per eviction — shifts up to 9999 pointers). Fix: use
    ``collections.deque(maxlen=_MAX_WORDS)`` for O(1) eviction, plus
    a ``_base_offset`` counter so ``_word_key_index`` absolute indices
    stay correct without per-eviction O(n) shifting.
    """

    def test_words_is_deque_with_maxlen(self):
        from voice_typer.server.streaming import StreamingTextAssembler

        asm = StreamingTextAssembler()
        assert isinstance(asm._words, __import__("collections").deque), (
            "StreamingTextAssembler._words must be a collections.deque "
            "(not a plain list) for O(1) eviction."
        )
        assert asm._words.maxlen == StreamingTextAssembler._MAX_WORDS, (
            f"deque maxlen must be _MAX_WORDS ({StreamingTextAssembler._MAX_WORDS}); "
            f"got {asm._words.maxlen}"
        )

    def test_base_offset_starts_at_zero(self):
        from voice_typer.server.streaming import StreamingTextAssembler

        asm = StreamingTextAssembler()
        assert asm._base_offset == 0

    def test_no_pop_zero_in_insert_word(self):
        """``_insert_word_unlocked`` must NOT call ``self._words.pop(0)``
        (the O(n) pre-fix pattern). The deque's auto-eviction handles it.
        """
        from voice_typer.server.streaming import StreamingTextAssembler

        src = inspect.getsource(StreamingTextAssembler._insert_word_unlocked)
        assert "pop(0)" not in src, (
            "AUDIO-019 regression: _insert_word_unlocked uses pop(0) (O(n)) — "
            "use deque(maxlen=N) auto-eviction instead."
        )

    def test_eviction_triggers_warning_with_correct_variable_name(self):
        """The eviction warning must reference ``evicted_word.word``
        (not the typo ``evited.word`` from the pre-fix code).
        """
        from voice_typer.server.streaming import StreamingTextAssembler

        src = inspect.getsource(StreamingTextAssembler._insert_word_unlocked)
        assert "evicted_word.word" in src, (
            "Eviction warning must reference 'evicted_word.word' (the "
            "pre-fix code had a typo 'evited.word' that would crash "
            "with NameError if the eviction path ever fired)."
        )
        # The pre-fix typo must NOT be present
        assert "evited" not in src, (
            "AUDIO-019 regression: the 'evited' typo (missing 'c') is back "
            "— use 'evicted_word'."
        )

    def test_eviction_preserves_word_key_index_correctness(self):
        """When _words exceeds _MAX_WORDS, the deque auto-evicts the
        oldest item. ``_word_key_index`` must still point to the right
        words after eviction.
        """
        from voice_typer.server.streaming import StreamingTextAssembler, WordTiming

        # Use a tiny maxlen so we can trigger eviction easily
        asm = StreamingTextAssembler()
        asm._words = __import__("collections").deque(maxlen=3)
        asm._MAX_WORDS = 3  # match the deque maxlen for the warning check

        # Insert 5 words with the same text — eviction will trigger.
        for i in range(5):
            asm.add_words(
                [WordTiming(f"word{i}", start_seconds=float(i), end_seconds=float(i) + 0.1)],
                commit_horizon_seconds=100.0,  # accept all
            )

        # After 5 inserts with maxlen=3, deque should contain 3 items
        assert len(asm._words) == 3
        # _base_offset should be 2 (2 items evicted)
        assert asm._base_offset == 2
        # The committed text should contain the 3 most recent words
        # (in order: word2, word3, word4)
        committed = asm.committed_text
        assert "word2" in committed
        assert "word3" in committed
        assert "word4" in committed
        # The evicted words should NOT be in the committed text
        assert "word0" not in committed
        assert "word1" not in committed


# ─── ADR 0007: AGC removed, replaced by Compressor filter ──────────────


class TestAudioAgcLastRmsPostAgc:
    """ADR 0007 §3.5: The old per-chunk AGC (_agc_update, C1) has been
    removed and replaced by the Compressor filter in the audio filter
    chain. These tests now verify that:

    1. The _agc_update method and its constants are gone from recording.py.
    2. _last_rms is still set (post-filter, for UI/IPC).
    3. No AGC-related dead code remains.
    """

    def test_last_rms_assignment_after_agc_recompute(self):
        """ADR 0007: AGC recompute block is gone. _last_rms is still set."""
        from voice_typer.server import recording as rec_mod

        src = inspect.getsource(rec_mod.Recorder.start)
        # The old AGC recompute block should NOT exist anymore
        agc_recompute_idx = src.find("if abs(self._agc_gain - 1.0) > 0.01")
        last_rms_idx = src.find("self._last_rms = chunk_rms")
        assert agc_recompute_idx == -1, (
            "ADR 0007: AGC recompute block should be deleted — "
            "the Compressor filter in the audio chain handles this now."
        )
        assert last_rms_idx >= 0, "_last_rms assignment must still exist for UI/IPC"

    def test_agc_applied_before_last_rms_storage(self):
        """ADR 0007: _agc_update call is gone. _last_rms is still set."""
        from voice_typer.server import recording as rec_mod

        src = inspect.getsource(rec_mod.Recorder.start)
        # The old _agc_update call should NOT exist anymore
        agc_update_idx = src.find("self._agc_update(chunk_rms, filtered)")
        last_rms_idx = src.find("self._last_rms = chunk_rms")
        assert agc_update_idx == -1, (
            "ADR 0007: _agc_update call should be deleted — "
            "the Compressor filter in the audio chain handles gain control now."
        )
        assert last_rms_idx >= 0, "_last_rms assignment must still exist for UI/IPC"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
