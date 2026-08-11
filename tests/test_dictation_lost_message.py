"""Regression tests for the ``dictation_lost`` event payload.

The previous implementation always emitted::

    {
        "type": "dictation_lost",
        "data": {
            "message": "A dictation was interrupted by a crash. "
                       "Partial audio may be recoverable.",
            "recoverable": True,
        },
    }

That was misleading: audio is NEVER persisted mid-transcription (the
audio buffer lives only in process memory and is zero-filled in the
``dictation_pipeline._transcribe`` finally block), and the
``.dictation-in-flight`` sentinel only persists the ``cycle_id``
correlation string. Partial TEXT is saved by ``crash_recovery.add()``
— but only when a Python exception was caught (a "soft" crash). On a
hard kill (SIGKILL / OOM / segfault) the transcription thread never
reaches the exception handler, so nothing is saved.

These tests pin the corrected payload:

* ``message`` — accurate wording that distinguishes soft-crash text
  recovery from hard-crash total loss and explicitly states no audio
  is recoverable.
* ``recoverable`` — ``True`` only when an unpasted recovery entry
  matching the sentinel's ``cycle_id`` exists in the in-memory store.
* ``recovery_type`` — ``"text_only"`` when recoverable, ``"none"``
  when not (audio is NEVER recoverable — made explicit).
"""

from __future__ import annotations

import logging
import os

import pytest
from voice_typer.server import event_bus
from voice_typer.server.crash_recovery import CrashRecovery


@pytest.fixture
def recovery_dir(tmp_path, monkeypatch):
    """Point both ``_paths.config_dir`` and ``config._config_dir`` at
    ``tmp_path``.

    ``_detect_and_notify_lost_dictation`` resolves the
    ``.dictation-in-flight`` sentinel via the lazy import
    ``from voice_typer.server._paths import config_dir as _config_dir``
    — so we must patch ``_paths.config_dir`` (not just
    ``config._config_dir``, which is what the older ``recovery_dir``
    fixture in ``test_crash_recovery.py`` patches). Patching both is
    defensive: it keeps the fixture compatible with future call sites
    that might resolve the dir through either module.
    """
    monkeypatch.setattr(
        "voice_typer.server._paths.config_dir",
        lambda: tmp_path,
    )
    monkeypatch.setattr(
        "voice_typer.server.config._config_dir",
        lambda: tmp_path,
    )
    return tmp_path


@pytest.fixture(autouse=True)
def _isolate_event_bus():
    """Snapshot + clear the global event_bus subscriber set per test.

    The event_bus is a process-global singleton. Subscribers registered
    by other test modules that ran earlier in the session would otherwise
    receive our ``dictation_lost`` events and could (a) assert on them
    and fail, or (b) hold references that prevent clean GC. Snapshot,
    clear, yield, restore — mirroring the pattern in
    ``tests/test_event_bus.py::_clean_subscribers``.
    """
    with event_bus._lock:
        original = set(event_bus._subscribers)
        event_bus._subscribers.clear()
    try:
        yield
    finally:
        with event_bus._lock:
            event_bus._subscribers.clear()
            event_bus._subscribers.update(original)


def _make_sentinel(recovery_dir, cycle_id: str) -> None:
    """Write the ``.dictation-in-flight`` sentinel with ``cycle_id``.

    Mirrors what ``dictation_pipeline._transcribe`` does at the top of
    a dictation: ``_sentinel.write_text(str(cycle_id), encoding="utf-8")``.
    """
    sentinel = recovery_dir / ".dictation-in-flight"
    sentinel.write_text(str(cycle_id), encoding="utf-8")


class TestDictationLostMessage:
    """Pin the corrected ``dictation_lost`` payload."""

    def test_soft_crash_with_matching_cycle_id_is_recoverable(self, recovery_dir):
        """When an unpasted entry exists for the sentinel's ``cycle_id``,
        the crash was soft (the exception handler ran ``add()`` before
        the process died) — partial TEXT is recoverable.

        Asserts:
          • ``recoverable`` is ``True``.
          • ``recovery_type`` is ``"text_only"``.
          • The message no longer claims audio is recoverable.
        """
        cr = CrashRecovery(config_dir=recovery_dir)
        try:
            # Simulate the exception-handler path: partial text saved
            # with pasted=False and a cycle_id matching the sentinel.
            cr.add("partial transcript", pasted=False, cycle_id="test-1")

            received: list[dict] = []
            event_bus.subscribe(received.append)
            try:
                _make_sentinel(recovery_dir, "test-1")
                cr._detect_and_notify_lost_dictation()
            finally:
                event_bus.unsubscribe(received.append)

            assert len(received) == 1, f"expected exactly one dictation_lost event; got {len(received)}"
            event = received[0]
            assert event["type"] == "dictation_lost"
            data = event["data"]
            assert data["recoverable"] is True
            assert data["recovery_type"] == "text_only"
            # Audio must NEVER be claimed as recoverable.
            assert "audio" not in data["message"].lower().replace("no audio is recoverable", "")
            assert "no audio is recoverable" in data["message"].lower()
            # cycle_id is echoed for renderer-side correlation.
            assert data["cycle_id"] == "test-1"
        finally:
            cr.shutdown()

    def test_hard_crash_with_no_matching_entry_is_not_recoverable(self, recovery_dir):
        """When no unpasted entry exists for the sentinel's ``cycle_id``,
        the crash was hard (SIGKILL / OOM / segfault) — the
        transcription thread never reached the exception handler, so
        nothing was saved. Nothing is recoverable.

        Asserts:
          • ``recoverable`` is ``False``.
          • ``recovery_type`` is ``"none"``.
        """
        cr = CrashRecovery(config_dir=recovery_dir)
        try:
            # No add() call — simulates a hard kill before any text was
            # saved. The recovery store is empty for cycle_id="test-2".
            received: list[dict] = []
            event_bus.subscribe(received.append)
            try:
                _make_sentinel(recovery_dir, "test-2")
                cr._detect_and_notify_lost_dictation()
            finally:
                event_bus.unsubscribe(received.append)

            assert len(received) == 1, f"expected exactly one dictation_lost event; got {len(received)}"
            event = received[0]
            assert event["type"] == "dictation_lost"
            data = event["data"]
            assert data["recoverable"] is False
            assert data["recovery_type"] == "none"
            assert "no audio is recoverable" in data["message"].lower()
            assert data["cycle_id"] == "test-2"
        finally:
            cr.shutdown()

    def test_both_paths_in_sequence(self, recovery_dir):
        """Single CrashRecovery instance: soft-crash recovery first,
        then clear the store and verify a subsequent hard-crash sentinel
        reports unrecoverable.

        This mirrors the acceptance-criteria flow: the same session
        that recovers cycle ``test-1`` text must NOT mislead the user
        into thinking cycle ``test-2`` audio is recoverable when the
        store has been cleared between the two events.
        """
        cr = CrashRecovery(config_dir=recovery_dir)
        try:
            # Step 1: soft crash — partial text saved for "test-1".
            cr.add("partial transcript one", pasted=False, cycle_id="test-1")
            received: list[dict] = []
            event_bus.subscribe(received.append)
            try:
                _make_sentinel(recovery_dir, "test-1")
                cr._detect_and_notify_lost_dictation()
            finally:
                event_bus.unsubscribe(received.append)

            assert len(received) == 1
            soft = received[0]["data"]
            assert soft["recoverable"] is True
            assert soft["recovery_type"] == "text_only"

            # Step 2: clear the recovery store (simulates the user
            # acknowledging the recovery and ``clear()`` being called).
            cr.clear()
            assert cr.count == 0

            # Step 3: hard crash — sentinel for "test-2" with no
            # matching entry. Must report unrecoverable.
            received.clear()
            event_bus.subscribe(received.append)
            try:
                _make_sentinel(recovery_dir, "test-2")
                cr._detect_and_notify_lost_dictation()
            finally:
                event_bus.unsubscribe(received.append)

            assert len(received) == 1
            hard = received[0]["data"]
            assert hard["recoverable"] is False
            assert hard["recovery_type"] == "none"
        finally:
            cr.shutdown()

    def test_pasted_entry_for_cycle_is_not_recoverable(self, recovery_dir):
        """An entry that was already pasted is NOT a recovery candidate
        — the user already received that text. The lookup must filter
        on ``pasted=False``.
        """
        cr = CrashRecovery(config_dir=recovery_dir)
        try:
            # Entry exists for "test-3" but was already pasted —
            # nothing to recover.
            cr.add("already-delivered", pasted=True, cycle_id="test-3")

            received: list[dict] = []
            event_bus.subscribe(received.append)
            try:
                _make_sentinel(recovery_dir, "test-3")
                cr._detect_and_notify_lost_dictation()
            finally:
                event_bus.unsubscribe(received.append)

            assert len(received) == 1
            data = received[0]["data"]
            assert data["recoverable"] is False
            assert data["recovery_type"] == "none"
        finally:
            cr.shutdown()

    def test_empty_sentinel_is_not_recoverable(self, recovery_dir):
        """A sentinel file with empty content (crashed mid-write before
        the cycle_id was persisted) must NOT match any entry — the
        ``cycle_id != ""`` guard treats it as a hard crash.
        """
        cr = CrashRecovery(config_dir=recovery_dir)
        try:
            # An anonymous entry with no cycle_id — must not match the
            # empty sentinel (which would be a false positive if the
            # guard were missing).
            cr.add("anonymous partial", pasted=False)

            received: list[dict] = []
            event_bus.subscribe(received.append)
            try:
                _make_sentinel(recovery_dir, "")
                cr._detect_and_notify_lost_dictation()
            finally:
                event_bus.unsubscribe(received.append)

            assert len(received) == 1
            data = received[0]["data"]
            assert data["recoverable"] is False
            assert data["recovery_type"] == "none"
        finally:
            cr.shutdown()

    def test_no_sentinel_emits_no_event(self, recovery_dir):
        """When no ``.dictation-in-flight`` sentinel exists, no
        ``dictation_lost`` event is emitted at all (normal startup —
        the previous process exited cleanly).
        """
        cr = CrashRecovery(config_dir=recovery_dir)
        try:
            received: list[dict] = []
            event_bus.subscribe(received.append)
            try:
                # No sentinel written — _detect_and_notify_lost_dictation
                # must early-return without publishing.
                cr._detect_and_notify_lost_dictation()
            finally:
                event_bus.unsubscribe(received.append)

            assert received == []
        finally:
            cr.shutdown()


class TestHu10SentinelSecureRead:
    """HU-10: the ``.dictation-in-flight`` sentinel is read through
    ``_secure_read_text`` (POSIX ``O_NOFOLLOW`` / Windows reparse-point
    check) — the same helper the recovery-file load path uses. A symlink
    planted at the sentinel path is REFUSED, so an attacker can never
    exfiltrate an arbitrary file's content into the production WARNING
    log. On refusal ``cycle_id`` stays ``""`` → the hard-crash
    (nothing recoverable) branch fires.
    """

    def test_read_refusal_treats_as_hard_crash(self, recovery_dir, caplog, monkeypatch):
        def _refuse(_path, *args, **kwargs):
            raise OSError("SEC-002: refusing to follow symlink")

        monkeypatch.setattr("voice_typer.server.config._secure_read_text", _refuse)
        # A real sentinel exists — but the secure read refuses it.
        _make_sentinel(recovery_dir, "EXFIL-SECRET-CYCLE")

        cr = CrashRecovery(config_dir=recovery_dir)
        try:
            received: list[dict] = []
            event_bus.subscribe(received.append)
            try:
                with caplog.at_level(logging.WARNING):
                    cr._detect_and_notify_lost_dictation()
            finally:
                event_bus.unsubscribe(received.append)

            assert len(received) == 1
            data = received[0]["data"]
            assert data["recoverable"] is False
            assert data["recovery_type"] == "none"
            assert data["cycle_id"] == "", "HU-10: refused read must leave cycle_id empty (fail-closed)"
            # The sentinel is still cleaned up (delete runs regardless).
            assert not (recovery_dir / ".dictation-in-flight").exists()
            # The exfil target content must never reach the WARNING log.
            assert not any("EXFIL-SECRET-CYCLE" in r.getMessage() for r in caplog.records)
        finally:
            cr.shutdown()

    @pytest.mark.skipif(os.name != "posix", reason="requires POSIX symlink semantics")
    def test_real_symlink_sentinel_never_logs_target_content(self, recovery_dir, caplog):
        secret = recovery_dir / "secret.txt"
        secret.write_text("TOP-SECRET-SENTINEL-TARGET", encoding="utf-8")
        os.symlink(secret, recovery_dir / ".dictation-in-flight")

        cr = CrashRecovery(config_dir=recovery_dir)
        try:
            received: list[dict] = []
            event_bus.subscribe(received.append)
            try:
                with caplog.at_level(logging.WARNING):
                    cr._detect_and_notify_lost_dictation()
            finally:
                event_bus.unsubscribe(received.append)

            assert len(received) == 1
            data = received[0]["data"]
            assert data["recoverable"] is False
            assert data["recovery_type"] == "none"
            assert data["cycle_id"] == ""
            assert not any("TOP-SECRET-SENTINEL-TARGET" in r.getMessage() for r in caplog.records)
        finally:
            cr.shutdown()
