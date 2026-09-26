"""binary launched by OS-level schedulers (Windows LogonTrigger / macOS"""

from __future__ import annotations

import asyncio
import atexit
import contextlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

websockets = pytest.importorskip("websockets")

from voice_typer.server.prewarm import (  # noqa: E402
    _WORKER_WARM_PACKAGES,
    warm_imports_for_worker,
)
from voice_typer.worker import __main__ as worker_main  # noqa: E402

# A fixed test token. The worker only checks the env var is non-empty
_TEST_TOKEN = "test-worker-token-12345"

# The worker validates ``VOICE_TYPER_CONFIG_DIR`` via the SEC-005
_test_root = Path.home() / ".lausu-test-tmp"
_test_root.mkdir(parents=True, exist_ok=True)
for _stale in _test_root.iterdir():
    if _stale.is_dir() and _stale.name.startswith("lausu-worker-test-"):
        shutil.rmtree(_stale, ignore_errors=True)

_worker_id = os.environ.get("PYTEST_XDIST_WORKER", "master")
_TEST_CONFIG_DIR = Path(tempfile.mkdtemp(prefix=f"lausu-worker-test-{_worker_id}-", dir=_test_root))


def _cleanup_test_config_dir() -> None:
    """Remove this worker's config dir, then the parent tmp root if it became empty."""
    shutil.rmtree(_TEST_CONFIG_DIR, ignore_errors=True)
    with contextlib.suppress(OSError):
        _test_root.rmdir()


atexit.register(_cleanup_test_config_dir)

# Hard deadline for the worker subprocess to emit ``worker_started``
_WORKER_START_DEADLINE_S = 20.0


def _find_worker_lock() -> Path | None:
    """Find any stale worker.lock file from a prior test/crash."""
    lock = _TEST_CONFIG_DIR / "run" / "worker.lock"
    if lock.exists():
        return lock
    legacy = _TEST_CONFIG_DIR / "worker.lock"
    return legacy if legacy.exists() else None


def _kill_stale_worker() -> None:
    """Kill any stale worker process holding ``worker.lock`` + remove the file."""
    lock = _find_worker_lock()
    if lock is None:
        return
    try:
        pid_str = lock.read_text(encoding="ascii").strip()
        pid = int(pid_str)
    except (OSError, ValueError):
        with contextlib.suppress(OSError):
            lock.unlink(missing_ok=True)
        return
    # Best-effort SIGTERM (POSIX only, Windows tests don't spawn real
    if hasattr(signal, "SIGTERM"):
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGTERM)
        # Give the process a moment to exit + release the lockfile.
        time.sleep(0.2)
    with contextlib.suppress(OSError):
        lock.unlink(missing_ok=True)


@contextlib.contextmanager
def _spawn_worker(token: str | None = _TEST_TOKEN):
    """Spawn ``python -m voice_typer.worker`` with the given token env."""
    _kill_stale_worker()
    env = {
        **os.environ,
        # Force deterministic test behavior: no bytecode writes, no
        "PYTHONDONTWRITEBYTECODE": "1",
        # Isolate the worker's config dir (worker.lock + worker.log +
        "VOICE_TYPER_CONFIG_DIR": str(_TEST_CONFIG_DIR),
    }
    if token is not None:
        env["VOICE_TYPER_IPC_TOKEN"] = token
    else:
        env.pop("VOICE_TYPER_IPC_TOKEN", None)
    proc = subprocess.Popen(
        [sys.executable, "-m", "voice_typer.worker"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        yield proc
    finally:
        # Graceful shutdown: terminate (SIGTERM on POSIX). The worker's
        with contextlib.suppress(Exception):
            proc.terminate()
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2.0)
        # Final lockfile cleanup (in case the worker left one).
        _kill_stale_worker()


def _read_worker_started(proc: subprocess.Popen, *, timeout_s: float = _WORKER_START_DEADLINE_S) -> dict | None:
    """Read stdout lines until ``worker_started`` event; return its parsed JSON."""
    deadline = time.monotonic() + timeout_s
    assert proc.stdout is not None
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if not line:
            return None  # worker exited
        line = line.strip()
        if not line:
            continue
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(evt, dict) and evt.get("event") == "worker_started":
            return evt
    return None


def _port_is_listening(port: int, *, host: str = "127.0.0.1") -> bool:
    """Return True if a TCP socket can connect to ``host:port``."""
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def _make_fake_websocket(auth_frame: str | bytes) -> MagicMock:
    """Build a mock websocket that yields *auth_frame* on the first recv."""
    ws = MagicMock()
    auth_frame_bytes = auth_frame.encode() if isinstance(auth_frame, str) else auth_frame

    async def _fake_recv():
        return auth_frame_bytes

    ws.recv = _fake_recv
    ws.remote_address = ("127.0.0.1", 12345)
    ws.origin = ""  # empty origin = non-browser (allowed)

    sent_frames: list[str] = []
    closed_with: list[tuple[tuple, dict]] = []

    async def _track_send(payload):
        sent_frames.append(payload)

    async def _track_close(*args, **kwargs):
        closed_with.append((args, kwargs))

    ws._sent_frames = sent_frames
    ws._closed_with = closed_with
    ws.send = _track_send
    ws.close = _track_close
    return ws


def test_worker_starts_and_emits_worker_started() -> None:
    """The worker spawns, binds 127.0.0.1:0, and emits ``worker_started``."""
    with _spawn_worker() as proc:
        evt = _read_worker_started(proc)
    assert evt is not None, (
        f"worker did not emit worker_started event, stderr: {proc.stderr.read() if proc.stderr else '<no stderr>'}"
    )
    assert evt["event"] == "worker_started"
    port = evt["port"]
    assert isinstance(port, int) and 1024 <= port <= 65535, f"expected port in ephemeral range, got {port!r}"
    assert evt["protocol"] == worker_main.PROTOCOL_VERSION
    # The port should be listening (the worker binds BEFORE emitting
    assert port > 0


def test_worker_started_port_is_connectable() -> None:
    """The port reported in ``worker_started`` is actually connectable."""
    import asyncio

    async def _connect_and_auth(port: int) -> bool:
        """Connect to the worker with the test token; return True if auth succeeded."""
        async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
            await ws.send(json.dumps({"type": "auth", "token": _TEST_TOKEN}))
            # Wait for either an auth_failed frame or the heartbeat_ack
            try:
                reply = await asyncio.wait_for(ws.recv(), timeout=2.0)
            except asyncio.TimeoutError:
                # No reply is also fine, auth succeeded, the handler
                return True
            try:
                evt = json.loads(reply) if isinstance(reply, str) else json.loads(reply.decode())
            except (json.JSONDecodeError, UnicodeDecodeError):
                return False
            return not (
                isinstance(evt, dict)
                and evt.get("type") == "error"
                and evt.get("data", {}).get("code") == "auth_failed"
            )

    with _spawn_worker() as proc:
        evt = _read_worker_started(proc)
        assert evt is not None, "worker did not emit worker_started"
        port = evt["port"]
        authed = asyncio.run(_connect_and_auth(port))
    assert authed, "worker rejected the correct token (auth_failed frame received)"


def test_warm_imports_for_worker_calls_warm_imports(monkeypatch) -> None:
    """``warm_imports_for_worker`` delegates to ``_warm_imports`` exactly once."""
    calls: list[int] = []
    monkeypatch.setattr(
        worker_main,
        "_warm_imports_spy_calls",
        calls,
        raising=False,
    )

    from voice_typer.server import prewarm as prewarm_pkg

    actual_calls: list[bool] = []

    def _fake_warm_imports() -> None:
        actual_calls.append(True)

    # Patch the underscore-prefixed internal function on the package
    monkeypatch.setattr(prewarm_pkg, "_warm_imports", _fake_warm_imports)

    # Also patch on the cache_probe submodule (where the function is
    from voice_typer.server.prewarm import cache_probe

    monkeypatch.setattr(cache_probe, "_warm_imports", _fake_warm_imports)

    # Call the public entry point.
    warm_imports_for_worker()
    assert actual_calls == [True], f"warm_imports_for_worker did not call _warm_imports (got calls={actual_calls})"


def test_warm_imports_for_worker_swallows_exceptions(monkeypatch) -> None:
    """
    ``warm_imports_for_worker`` swallows exceptions from ``_warm_imports``.
    Master plan §6.2 P-1: prewarm is best-effort. A failure MUST NOT
    """
    from voice_typer.server import prewarm as prewarm_pkg
    from voice_typer.server.prewarm import cache_probe

    def _exploding_warm_imports() -> None:
        raise RuntimeError("prewarm explosion (test)")

    monkeypatch.setattr(prewarm_pkg, "_warm_imports", _exploding_warm_imports)
    monkeypatch.setattr(cache_probe, "_warm_imports", _exploding_warm_imports)

    # Must NOT raise.
    warm_imports_for_worker()


def test_warm_imports_package_list_is_post_migration() -> None:
    """The warm-list contains the post-migration packages (no torch/transformers)."""
    assert "onnxruntime" in _WORKER_WARM_PACKAGES
    assert "ctranslate2" in _WORKER_WARM_PACKAGES
    assert "numpy" in _WORKER_WARM_PACKAGES
    assert "scipy" in _WORKER_WARM_PACKAGES
    assert "torch" not in _WORKER_WARM_PACKAGES, (
        "torch must be DROPPED from the warm list, VAD is now ONNX (master plan §6.2 P-1)"
    )
    assert "transformers" not in _WORKER_WARM_PACKAGES, (
        "transformers must be DROPPED, Parakeet is now onnx-asr (master plan §6.2 P-1)"
    )


def test_worker_exits_without_token_env() -> None:
    """The worker refuses to start when ``VOICE_TYPER_IPC_TOKEN`` is unset."""
    with _spawn_worker(token=None) as proc:
        try:
            proc.wait(timeout=15.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2.0)
            pytest.fail("worker did not exit within 15s when VOICE_TYPER_IPC_TOKEN was unset")
    assert proc.returncode == worker_main.EXIT_NO_TOKEN, (
        f"expected EXIT_NO_TOKEN ({worker_main.EXIT_NO_TOKEN}), got {proc.returncode}"
    )
    # The worker must NOT have emitted worker_started.
    assert _read_worker_started(proc, timeout_s=0.1) is None, (
        "worker emitted worker_started despite missing VOICE_TYPER_IPC_TOKEN"
    )


async def test_wrong_token_emits_auth_failed_before_close(monkeypatch) -> None:
    """A wrong-token auth frame is rejected with ``auth_failed`` + close 1008."""
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "expected-secret")
    ws = _make_fake_websocket(json.dumps({"type": "auth", "token": "wrong-secret"}))

    stop_event = asyncio.Event()
    shutdown_timer = worker_main._ShutdownTimer()
    await worker_main._handle_connection(ws, prewarm_ran=True, stop_event=stop_event, shutdown_timer=shutdown_timer)

    assert len(ws._sent_frames) == 1, f"expected exactly one auth_failed frame, got {ws._sent_frames}"
    frame = json.loads(ws._sent_frames[0])
    assert frame["type"] == "error"
    assert frame["data"]["code"] == "auth_failed"
    assert len(ws._closed_with) == 1
    _, close_kwargs = ws._closed_with[0]
    assert close_kwargs.get("code") == 1008
    # Auth failure must NOT trigger graceful shutdown.
    assert not stop_event.is_set(), "auth failure must not set stop_event"


async def test_non_auth_first_frame_emits_auth_failed(monkeypatch) -> None:
    """A first frame that is not ``{\"type\":\"auth\",...}`` is rejected."""
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "tok")
    ws = _make_fake_websocket(json.dumps({"type": "get_status"}))

    stop_event = asyncio.Event()
    shutdown_timer = worker_main._ShutdownTimer()
    await worker_main._handle_connection(ws, prewarm_ran=True, stop_event=stop_event, shutdown_timer=shutdown_timer)

    assert len(ws._sent_frames) == 1
    frame = json.loads(ws._sent_frames[0])
    assert frame["data"]["code"] == "auth_failed"
    assert len(ws._closed_with) == 1
    _, close_kwargs = ws._closed_with[0]
    assert close_kwargs.get("code") == 1008
    assert not stop_event.is_set()


async def test_invalid_json_auth_frame_emits_auth_failed(monkeypatch) -> None:
    """Garbage on the wire → ``auth_failed`` + close 1008."""
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "tok")
    ws = _make_fake_websocket(b"not json at all")

    stop_event = asyncio.Event()
    shutdown_timer = worker_main._ShutdownTimer()
    await worker_main._handle_connection(ws, prewarm_ran=True, stop_event=stop_event, shutdown_timer=shutdown_timer)

    assert len(ws._sent_frames) == 1
    frame = json.loads(ws._sent_frames[0])
    assert frame["data"]["code"] == "auth_failed"
    assert len(ws._closed_with) == 1
    assert not stop_event.is_set()


async def test_missing_token_env_rejects_connection(monkeypatch) -> None:
    """If ``VOICE_TYPER_IPC_TOKEN`` is unset, the worker rejects every connection."""
    monkeypatch.delenv("VOICE_TYPER_IPC_TOKEN", raising=False)
    ws = _make_fake_websocket(json.dumps({"type": "auth", "token": "anything"}))

    stop_event = asyncio.Event()
    shutdown_timer = worker_main._ShutdownTimer()
    await worker_main._handle_connection(ws, prewarm_ran=True, stop_event=stop_event, shutdown_timer=shutdown_timer)

    assert len(ws._sent_frames) == 1
    frame = json.loads(ws._sent_frames[0])
    assert frame["data"]["code"] == "auth_failed"
    assert not stop_event.is_set()


async def test_shutdown_command_emits_ack_and_closes(monkeypatch) -> None:
    """The ``shutdown`` command emits ``shutdown_ack`` + closes the socket + sets stop_event."""
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", _TEST_TOKEN)

    # The worker's _authenticate reads the FIRST frame via
    auth_frame = json.dumps({"type": "auth", "token": _TEST_TOKEN}).encode()
    shutdown_frame = json.dumps({"cmd": "shutdown"}).encode()

    recv_calls: list[bytes] = [auth_frame]

    async def _fake_recv() -> bytes:
        if recv_calls:
            return recv_calls.pop(0)
        # Should never be reached, the dispatch loop uses __aiter__.
        raise AssertionError("recv() called after auth frame, dispatch should use __aiter__")

    class _FrameAsyncIter:
        def __aiter__(self) -> _FrameAsyncIter:
            return self

        async def __anext__(self) -> bytes:
            # Yield the shutdown frame, then stop. The worker's
            if shutdown_frame is not None:
                # Use a sentinel: pop the frame so subsequent calls
                frame = _FrameAsyncIter._remaining.pop(0) if _FrameAsyncIter._remaining else None  # type: ignore[attr-defined]
                if frame is None:
                    raise StopAsyncIteration
                return frame
            raise StopAsyncIteration

    _FrameAsyncIter._remaining = [shutdown_frame]  # type: ignore[attr-defined]

    ws = MagicMock()
    ws.recv = _fake_recv
    ws.__aiter__ = lambda self: _FrameAsyncIter()  # noqa: E731
    ws.remote_address = ("127.0.0.1", 12345)
    ws.origin = ""

    sent_frames: list[str] = []
    closed_with: list[tuple[tuple, dict]] = []

    async def _track_send(payload):
        sent_frames.append(payload)

    async def _track_close(*args, **kwargs):
        closed_with.append((args, kwargs))

    ws.send = _track_send
    ws.close = _track_close

    stop_event = asyncio.Event()
    shutdown_timer = worker_main._ShutdownTimer()
    await worker_main._handle_connection(ws, prewarm_ran=True, stop_event=stop_event, shutdown_timer=shutdown_timer)

    # The shutdown_ack frame must be sent BEFORE the close.
    assert any(json.loads(f).get("type") == "shutdown_ack" for f in sent_frames), (
        f"expected shutdown_ack frame, got {sent_frames}"
    )
    assert len(closed_with) >= 1, "expected the worker to close the socket after shutdown_ack"
    # Regression guard: stop_event MUST be set so run_worker_server's
    assert stop_event.is_set(), (
        "shutdown command must set stop_event so run() unblocks, "
        "missing stop_event.set() reproduces the shutdown-hang regression"
    )
    # carries a real <duration> suffix (C-LOG-2).
    assert shutdown_timer.elapsed() >= 0.0, "shutdown_timer.start() must have been called"


def test_shutdown_command_exits_worker() -> None:
    """The ``shutdown`` command causes the worker process to exit cleanly with EXIT_OK."""
    if not hasattr(signal, "SIGTERM") or os.name != "posix":
        pytest.skip("Integration test uses POSIX-only subprocess patterns (SIGTERM fallback in cleanup)")

    _kill_stale_worker()
    env = {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "VOICE_TYPER_IPC_TOKEN": _TEST_TOKEN,
        "VOICE_TYPER_CONFIG_DIR": str(_TEST_CONFIG_DIR),
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "voice_typer.worker"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        evt = _read_worker_started(proc)
        assert evt is not None, "worker did not emit worker_started before shutdown command"
        port = evt["port"]

        async def _send_shutdown() -> None:
            async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
                await ws.send(json.dumps({"type": "auth", "token": _TEST_TOKEN}))
                await ws.send(json.dumps({"cmd": "shutdown"}))
                # Best-effort: read the shutdown_ack frame. The worker
                with contextlib.suppress(asyncio.TimeoutError, websockets.exceptions.ConnectionClosed):
                    await asyncio.wait_for(ws.recv(), timeout=2.0)

        asyncio.run(_send_shutdown())

        try:
            exit_code = proc.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2.0)
            stderr = proc.stderr.read() if proc.stderr else "<no stderr>"
            pytest.fail(
                f"worker did not exit within 3s of `shutdown` command, stop_event.set() not called? stderr: {stderr}"
            )
        assert exit_code == worker_main.EXIT_OK, (
            f"expected EXIT_OK ({worker_main.EXIT_OK}) after shutdown command, got {exit_code}"
        )
    finally:
        with contextlib.suppress(Exception):
            proc.terminate()
            proc.wait(timeout=2.0)
        _kill_stale_worker()

    # The single-instance lockfile MUST be released on clean exit
    lock = _find_worker_lock()
    assert lock is None, f"worker.lock still exists after shutdown command, release() did not run: {lock}"


def test_sigterm_clean_exit() -> None:
    """SIGTERM causes the worker to exit cleanly with code 0."""
    if not hasattr(signal, "SIGTERM") or os.name != "posix":
        pytest.skip(
            "SIGTERM test is POSIX-only: on Windows signal.SIGTERM maps to TerminateProcess (hard kill, no handler)"
        )

    _kill_stale_worker()
    env = {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "VOICE_TYPER_IPC_TOKEN": _TEST_TOKEN,
        "VOICE_TYPER_CONFIG_DIR": str(_TEST_CONFIG_DIR),
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "voice_typer.worker"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        evt = _read_worker_started(proc)
        assert evt is not None, "worker did not emit worker_started before SIGTERM"
        # Send SIGTERM, expect clean exit.
        proc.send_signal(signal.SIGTERM)
        try:
            exit_code = proc.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2.0)
            pytest.fail("worker did not exit within 3s of SIGTERM")
        assert exit_code == worker_main.EXIT_OK, (
            f"expected EXIT_OK ({worker_main.EXIT_OK}) after SIGTERM, got {exit_code}"
        )
    finally:
        with contextlib.suppress(Exception):
            proc.terminate()
            proc.wait(timeout=2.0)
        _kill_stale_worker()

    # The single-instance lockfile MUST be released on clean exit.
    lock = _find_worker_lock()
    assert lock is None, f"worker.lock still exists after clean exit, release() did not run: {lock}"


def test_worker_single_instance_lock_rejects_duplicate(monkeypatch, tmp_path) -> None:
    """A second worker spawn is rejected by the single-instance lock."""
    monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
    # Also patch the worker's lazy import of _config_dir (it imports
    lock_path = tmp_path / "run" / "worker.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    if os.name == "posix":
        lock_path.write_text(f"{os.getpid()}\n", encoding="ascii")
    else:
        # Windows: best-effort existence check.
        lock_path.write_text(f"{os.getpid()}\n", encoding="ascii")

    handle = worker_main._ensure_worker_single_instance()
    try:
        assert handle is None, (
            "expected _ensure_worker_single_instance to return None when a live "
            "PID is in the lockfile (duplicate-instance rejection)"
        )
    finally:
        if handle is not None:
            handle.release()
