"""Download pause/resume/cancel must bypass the dispatch lock."""

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
        """Pause/resume/cancel DO mutate state (flags), they must not"""
        assert not (_INSTANT_CONTROL_COMMANDS & _READONLY_COMMANDS)

    def test_long_download_stays_locked(self) -> None:
        """``download_model`` itself must keep the lock: two concurrent"""
        assert "download_model" not in _INSTANT_CONTROL_COMMANDS
        assert "download_model" not in _READONLY_COMMANDS


class TestControlBypassesLockWhileDownloadHoldsIt:
    """Each control command answers while another thread holds"""

    def _dispatch_while_locked(self, msg: dict, timeout: float = 5.0) -> dict | None:
        server, _fake_app, _fake_service = make_ipc_server_with_fakes()
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        server._dispatch_lock.acquire()
        try:
            future = pool.submit(server._dispatch, msg)
            # Old code blocks here until timeout (the handler waits
            return future.result(timeout=timeout)
        finally:
            # Release FIRST so a regressed (lock-waiting) worker can
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
