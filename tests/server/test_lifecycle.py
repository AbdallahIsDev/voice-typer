"""Server lifecycle and error-handling tests.

Classes:
- TestLifecycle    — start()/stop() daemon-thread management
- TestErrorHandling — JSON-parse / unknown-command loop resilience

Split out from the original monolithic tests/test_server.py (DT-37, Phase 4.5).
"""

import io
import json
import threading

from tests.server.conftest import (  # noqa: F401
    server,
)

# ── Lifecycle ──────────────────────────────────────────────────────────


class TestLifecycle:
    def test_start_launches_daemon_thread(self, server, monkeypatch):
        # Replace stdin so the daemon thread doesn't block on real stdin.
        monkeypatch.setattr("sys.stdin", io.StringIO())
        server.start()
        assert server._running is True
        # The thread may already have exited (empty StringIO exhausts
        # immediately), but the important thing is that start() set
        # _running and attempted to create the daemon thread.
        threads = [t for t in threading.enumerate() if t.name == "ipc-server"]
        assert len(threads) <= 1
        if threads:
            assert threads[0].daemon is True
        server.stop()

    def test_stop_sets_running_false(self, server, monkeypatch):
        monkeypatch.setattr("sys.stdin", io.StringIO())
        server.start()
        assert server._running is True
        server.stop()
        assert server._running is False


# ── Error handling ─────────────────────────────────────────────────────


class TestErrorHandling:
    def test_invalid_json_via_run(self, server):
        stdin = io.StringIO("{{{bad}}\n")
        stdout = io.StringIO()
        server._running = True
        server._run(_stdin=stdin, _stdout=stdout)

        msg = json.loads(stdout.getvalue().strip())
        assert msg["type"] == "error"
        assert "invalid JSON" in msg["data"]["message"]

    def test_command_error_does_not_kill_loop(self, server):
        """An unknown command returns an error but the loop continues."""
        stdin = io.StringIO('{"type":"nope","id":1}\n{"type":"get_status","id":2}\n')
        stdout = io.StringIO()
        server._running = True
        server._run(_stdin=stdin, _stdout=stdout)

        lines = stdout.getvalue().strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["type"] == "error"
        assert json.loads(lines[1])["type"] == "status"
