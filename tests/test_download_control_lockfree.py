"""Download pause/resume/cancel must bypass the dispatch lock.

Regression test for the 2026-09-16 incident: a large model download
runs its poll loop synchronously in the dispatch thread, holding
``IPCServer._dispatch_lock`` for the whole transfer. Pause / resume /
cancel are state-mutating commands, so they serialized behind that
lock and could never run — every attempt timed out (pause 30s,
get_status 15s x4 at the Rust layer), the 4-worker WS pool saturated
with blocked waiters, the UI desynced to "Resume" while bytes kept
flowing, and the backend eventually died under the retry storm.

These three commands bypass the lock (see ``_INSTANT_CONTROL_COMMANDS``
in ``voice_typer/server/ipc/registry.py``): each performs only atomic
flag/event flips under its own dedicated lock, never touches shared
app/service state, and is idempotent, so serializing them buys
nothing. The bypass is what lets pause/cancel land WHILE a download
holds the lock.
"""

from __future__ import annotations

import concurrent.futures

from voice_typer.server.ipc.registry import _INSTANT_CONTROL_COMMANDS, _READONLY_COMMANDS

from tests.fixtures.ipc_test_helpers import make_ipc_server_with_fakes


class TestInstantControlMembership:
    """The bypass set holds exactly the three download-control commands."""

    def test_membership_is_exact(self) -> None:
        assert (
            frozenset(
                {
                    "pause_model_download",
                    "resume_model_download",
                    "cancel_model_download",
                }
            )
            == _INSTANT_CONTROL_COMMANDS
        )

    def test_control_commands_are_not_misfiled_as_readonly(self) -> None:
        """Pause/resume/cancel DO mutate state (flags), they must not
        drift into ``_READONLY_COMMANDS``, which promises pure reads."""
        assert not (_INSTANT_CONTROL_COMMANDS & _READONLY_COMMANDS)

    def test_long_download_stays_locked(self) -> None:
        """``download_model`` itself must keep the lock: two concurrent
        transfers must still serialize (single-flight lives on top of
        the lock, the lock is the backstop)."""
        assert "download_model" not in _INSTANT_CONTROL_COMMANDS
        assert "download_model" not in _READONLY_COMMANDS


class TestControlBypassesLockWhileDownloadHoldsIt:
    """Each control command answers while another thread holds
    ``_dispatch_lock`` (simulating the in-flight download)."""

    def _dispatch_while_locked(self, msg: dict, timeout: float = 5.0) -> dict | None:
        server, _fake_app, _fake_service = make_ipc_server_with_fakes()
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        server._dispatch_lock.acquire()
        try:
            future = pool.submit(server._dispatch, msg)
            # Old code blocks here until timeout (the handler waits
            # on the lock); fixed code answers in milliseconds.
            return future.result(timeout=timeout)
        finally:
            # Release FIRST so a regressed (lock-waiting) worker can
            # still finish; only then join the pool. Reversing this
            # order deadlocks shutdown against the lock we hold.
            server._dispatch_lock.release()
            pool.shutdown(wait=True)

    def test_pause_answers_while_lock_held(self) -> None:
        resp = self._dispatch_while_locked({"id": 1, "type": "pause_model_download", "data": {}})
        assert resp is not None and resp["type"] == "ack"

    def test_resume_answers_while_lock_held(self) -> None:
        resp = self._dispatch_while_locked({"id": 2, "type": "resume_model_download", "data": {}})
        assert resp is not None and resp["type"] == "ack"

    def test_cancel_answers_while_lock_held(self) -> None:
        resp = self._dispatch_while_locked({"id": 3, "type": "cancel_model_download", "data": {}})
        assert resp is not None and resp["type"] == "ack"
