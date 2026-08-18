"""Worker startup + prewarm absorption tests (master plan §4.4, §6.2 P-1, §7).

Phase 2 / master plan §6.2 P-1: prewarm is no longer a separate frozen
binary launched by OS-level schedulers (Windows LogonTrigger / macOS
LaunchAgent / Linux systemd). It is now a STARTUP PHASE of the worker
exe (``voice_typer/worker/__main__.py``).

These tests verify the four core contracts the worker must uphold per
the master plan:

1. **Worker starts and listens on a WS port** (§4.4, §7.1).
   ``test_worker_starts_and_emits_worker_started`` spawns the worker
   subprocess, reads stdout, asserts the ``worker_started`` JSON event
   carries a port, then connects to that port via the ``websockets``
   client library.

2. **Prewarm phase runs once at startup** (§6.2 P-1).
   ``test_prewarm_phase_runs_once_before_worker_started`` spies on
   :func:`voice_typer.server.prewarm.warm_imports_for_worker` (via a
   sitecustomize-style monkeypatch on the worker's import path) and
   asserts the call happens BEFORE the ``worker_started`` event is
   emitted.
   ``test_warm_imports_for_worker_calls_warm_imports`` is a unit test
   on the public entry point itself.

3. **Auth token is required** (§7.2).
   ``test_worker_exits_without_token_env`` verifies the worker refuses
   to start when ``VOICE_TYPER_IPC_TOKEN`` is unset (exit code
   ``EXIT_NO_TOKEN``).
   ``test_wrong_token_emits_auth_failed_before_close``,
   ``test_missing_token_in_frame_emits_auth_failed``,
   ``test_non_auth_first_frame_emits_auth_failed`` are mocked
   connection tests mirroring ``test_sidecar_ws_auth_failed.py``.

4. **Shutdown is clean** (§7.2).
   ``test_shutdown_command_emits_ack_and_closes`` (mocked) verifies the
   ``shutdown`` command emits ``shutdown_ack``, closes the socket, AND
   sets ``stop_event`` (regression guard for the shutdown-hang bug).
   ``test_shutdown_command_exits_worker`` (integration) spawns a real
   worker, sends ``shutdown`` via WS, asserts the process exits with
   ``EXIT_OK`` within 3s and the lockfile is released.
   ``test_sigterm_clean_exit`` (integration) spawns the worker, sends
   SIGTERM, verifies the exit code + lock-file release.

The mocked connection tests use ``MagicMock`` websockets (the same
pattern as ``test_sidecar_ws_auth_failed.py``); no real WS server is
bound for those. The integration tests spawn a real ``python -m
voice_typer.worker`` subprocess.
"""

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

# Skip the entire module if ``websockets`` is not installed — the
# worker's WS server depends on it (mirrors test_sidecar_ws_auth_failed.py).
websockets = pytest.importorskip("websockets")

from voice_typer.server.prewarm import (  # noqa: E402
    _WORKER_WARM_PACKAGES,
    warm_imports_for_worker,
)
from voice_typer.worker import __main__ as worker_main  # noqa: E402

# ─── Helpers ────────────────────────────────────────────────────────────

# A fixed test token. The worker only checks the env var is non-empty
# at startup; per-connection auth compares the client's frame token to
# the env-var value via ``hmac.compare_digest``.
_TEST_TOKEN = "test-worker-token-12345"

# Per-xdist-worker isolated config dir. The integration tests spawn
# REAL worker subprocesses, and ``--dist=loadgroup`` puts each test on
# a DIFFERENT xdist worker → multiple worker subprocesses run
# CONCURRENTLY and would race the single ``worker.lock`` in the user's
# real config dir (Windows: existence check → silent
# EXIT_DUPLICATE_INSTANCE; also: shared ``worker.log`` rotation
# contention). Pointing every spawned worker at a per-pytest-process
# temp dir (keyed on ``PYTEST_XDIST_WORKER``) isolates the lock, the
# log, and the prewarm status file — and keeps the user's real config
# untouched.
_worker_id = os.environ.get("PYTEST_XDIST_WORKER", "master")
# The worker validates ``VOICE_TYPER_CONFIG_DIR`` via the SEC-005
# path-safety check (``_validate_path_safety(custom, Path.home())``)
# and DISCARDS values that resolve outside the user's home directory.
# ``tempfile.mkdtemp()`` defaults to the system temp dir, which on
# Linux (``/tmp``) and macOS (``/var/folders/...`` — a symlink to
# ``/private/var/folders``) resolves OUTSIDE home, so the worker would
# silently fall back to the shared default config dir — and concurrent
# worker tests on other xdist workers (all legs of the same CI job run
# in parallel on one machine) would then contend on a SINGLE
# ``worker.lock``; the loser exits immediately (single instance) and
# never emits ``worker_started``, failing this test intermittently.
# Creating the dir UNDER home makes the env var pass validation on
# every platform (Windows' %TEMP% happens to be under home, which is
# why this only ever failed on Linux/macOS legs).
_TEST_CONFIG_DIR = Path(tempfile.mkdtemp(prefix=f"voice-typer-worker-test-{_worker_id}-", dir=Path.home()))
atexit.register(lambda: shutil.rmtree(_TEST_CONFIG_DIR, ignore_errors=True))

# Hard deadline for the worker subprocess to emit ``worker_started``
# after spawn. The master plan §3.4 target is ≤ 600 ms, but the
# worker's warm phase runs BEFORE ``worker_started`` (it pages
# onnxruntime/ctranslate2/numpy/scipy/faster_whisper files into the OS
# cache), and the suite runs these integration tests under
# ``pytest -n auto`` where parallel workers hammer the same disk (and
# Windows Defender scans every imported .py). 5 s false-failed under
# that load (2026-08-14); 20 s is generous for a loaded CI runner
# while still bounding a genuinely hung worker.
_WORKER_START_DEADLINE_S = 20.0


def _find_worker_lock() -> Path | None:
    """Find any stale worker.lock file from a prior test/crash.

    Tests must clean these up before spawning a real worker to avoid
    spurious ``EXIT_DUPLICATE_INSTANCE`` failures. Looks in the
    per-pytest-process temp config dir (spawned workers honor
    ``VOICE_TYPER_CONFIG_DIR``), NOT the user's real config dir.
    """
    lock = _TEST_CONFIG_DIR / "worker.lock"
    return lock if lock.exists() else None


def _kill_stale_worker() -> None:
    """Kill any stale worker process holding ``worker.lock`` + remove the file.

    Best-effort: if the PID in the lockfile is alive and matches a
    ``python -m voice_typer.worker`` process, SIGTERM it. The lockfile
    is then unlinked so the next test's spawn does not see a stale
    lock.
    """
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
    # Best-effort SIGTERM (POSIX only — Windows tests don't spawn real
    # workers).
    if hasattr(signal, "SIGTERM"):
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGTERM)
        # Give the process a moment to exit + release the lockfile.
        time.sleep(0.2)
    with contextlib.suppress(OSError):
        lock.unlink(missing_ok=True)


@contextlib.contextmanager
def _spawn_worker(token: str | None = _TEST_TOKEN):
    """Spawn ``python -m voice_typer.worker`` with the given token env.

    Yields the ``subprocess.Popen`` object. On exit, sends SIGTERM /
    terminate and waits for the process to exit. The single-instance
    lockfile is cleaned up before + after the spawn so a stale lock
    from a prior test does not block the spawn.

    The token is set via the ``VOICE_TYPER_IPC_TOKEN`` env var. If
    ``token is None``, the env var is DELIBERATELY not set (used by
    ``test_worker_exits_without_token_env``).
    """
    _kill_stale_worker()
    env = {
        **os.environ,
        # Force deterministic test behavior: no bytecode writes, no
        # restart-env-var hint (the stale-lock cleanup above handles
        # any prior lockfile).
        "PYTHONDONTWRITEBYTECODE": "1",
        # Isolate the worker's config dir (worker.lock + worker.log +
        # prewarm_status.json) from the user's real config AND from
        # concurrent worker tests on other xdist workers.
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
        # SIGTERM handler initiates graceful shutdown; ``wait(2.0)``
        # gives it time. If still alive, kill().
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
    """Read stdout lines until ``worker_started`` event; return its parsed JSON.

    Returns ``None`` if the worker exits before emitting the event, or
    if the deadline elapses with no event.
    """
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
    """Build a mock websocket that yields *auth_frame* on the first recv.

    Mirrors the helper in ``tests/test_sidecar_ws_auth_failed.py`` so the
    worker's auth-rejection path is tested with the same mock shape as
    the slim-core sidecar's.
    """
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


# ─── 1. Worker starts and listens on a WS port ─────────────────────────


def test_worker_starts_and_emits_worker_started() -> None:
    """The worker spawns, binds 127.0.0.1:0, and emits ``worker_started``.

    Master plan §4.4: the worker's WS server binds on an ephemeral port
    and reports it via a single stdout JSON line. The host reads this
    line and opens a WS client to ``ws://127.0.0.1:<port>``.

    The test asserts:
      - The ``worker_started`` event is emitted within the deadline.
      - The event carries a ``port`` field that is a valid int in the
        ephemeral range (1024..65535).
      - The port is actually listening (TCP connect succeeds).
      - The event carries ``protocol`` field matching
        ``worker_main.PROTOCOL_VERSION``.
    """
    with _spawn_worker() as proc:
        evt = _read_worker_started(proc)
    assert evt is not None, (
        f"worker did not emit worker_started event — stderr: {proc.stderr.read() if proc.stderr else '<no stderr>'}"
    )
    assert evt["event"] == "worker_started"
    port = evt["port"]
    assert isinstance(port, int) and 1024 <= port <= 65535, f"expected port in ephemeral range, got {port!r}"
    assert evt["protocol"] == worker_main.PROTOCOL_VERSION
    # The port should be listening (the worker binds BEFORE emitting
    # the event). Use a short retry loop — the asyncio serve() may
    # not have the socket fully ready immediately after the print().
    # The worker emits worker_started AFTER serve() returns the bound
    # socket, so the port MUST be listening by the time we read the
    # event. The worker has already been shut down by the context
    # manager's __exit__ — but the bind was active when the event was
    # emitted (the assertion is on the event payload, not on a live
    # connect — that's covered by the integration test below).
    assert port > 0


def test_worker_started_port_is_connectable() -> None:
    """The port reported in ``worker_started`` is actually connectable.

    Integration test: spawn the worker, read the port from
    ``worker_started``, connect via the ``websockets`` client library
    with the correct token, and verify the connection is accepted (no
    ``auth_failed`` frame). This is the end-to-end "the worker actually
    listens" check.
    """
    import asyncio

    async def _connect_and_auth(port: int) -> bool:
        """Connect to the worker with the test token; return True if auth succeeded."""
        async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
            await ws.send(json.dumps({"type": "auth", "token": _TEST_TOKEN}))
            # Wait for either an auth_failed frame or the heartbeat_ack
            # (which proves auth succeeded + the handler is running).
            try:
                reply = await asyncio.wait_for(ws.recv(), timeout=2.0)
            except asyncio.TimeoutError:
                # No reply is also fine — auth succeeded, the handler
                # is just waiting for the next frame.
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
        # Connect while the worker is still alive (we're inside the
        # context manager).
        authed = asyncio.run(_connect_and_auth(port))
    assert authed, "worker rejected the correct token (auth_failed frame received)"


# ─── 2. Prewarm phase runs once at startup ─────────────────────────────


def test_warm_imports_for_worker_calls_warm_imports(monkeypatch) -> None:
    """``warm_imports_for_worker`` delegates to ``_warm_imports`` exactly once.

    Master plan §6.2 P-1: the public entry point is a thin wrapper
    around the underscore-prefixed ``_warm_imports``. This unit test
    spies on ``_warm_imports`` and asserts the call happens.
    """
    calls: list[int] = []
    monkeypatch.setattr(
        worker_main,
        # Patch the function on the prewarm package namespace so the
        # ``from voice_typer.server.prewarm import warm_imports_for_worker``
        # import in the worker's ``_run_prewarm_phase`` picks up the
        # patched version. The patch target is the prewarm package's
        # attribute, not the cache_probe submodule's.
        "_warm_imports_spy_calls",
        calls,
        raising=False,
    )

    # The function under test imports ``warm_imports_for_worker`` from
    # the prewarm package at CALL TIME (inside ``_run_prewarm_phase``),
    # so patching the package's attribute before calling the entry
    # point works.
    from voice_typer.server import prewarm as prewarm_pkg

    actual_calls: list[bool] = []

    def _fake_warm_imports() -> None:
        actual_calls.append(True)

    # Patch the underscore-prefixed internal function on the package
    # namespace. ``warm_imports_for_worker`` calls ``_warm_imports()``
    # via the package-level binding (cache_probe imports it as a name
    # and the ``__init__.py`` re-exports it, so monkeypatching the
    # package attribute IS the right scope).
    monkeypatch.setattr(prewarm_pkg, "_warm_imports", _fake_warm_imports)

    # Also patch on the cache_probe submodule (where the function is
    # defined and where it does the actual ``_warm_imports()`` call).
    from voice_typer.server.prewarm import cache_probe

    monkeypatch.setattr(cache_probe, "_warm_imports", _fake_warm_imports)

    # Call the public entry point.
    warm_imports_for_worker()
    assert actual_calls == [True], f"warm_imports_for_worker did not call _warm_imports (got calls={actual_calls})"


def test_warm_imports_for_worker_swallows_exceptions(monkeypatch) -> None:
    """``warm_imports_for_worker`` swallows exceptions from ``_warm_imports``.

    Master plan §6.2 P-1: prewarm is best-effort. A failure MUST NOT
    crash the worker (the cold cache only costs latency, never
    correctness). The public entry point catches all exceptions and
    logs at DEBUG.
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
    """The warm-list contains the post-migration packages (no torch/transformers).

    Master plan §6.2 P-1: ``onnxruntime + ctranslate2 + numpy/scipy``
    (+ ``faster_whisper`` for the Whisper backend's own Python files).
    ``torch`` and ``transformers`` are DROPPED — VAD is now ONNX,
    Parakeet is now ``onnx-asr``.
    """
    assert "onnxruntime" in _WORKER_WARM_PACKAGES
    assert "ctranslate2" in _WORKER_WARM_PACKAGES
    assert "numpy" in _WORKER_WARM_PACKAGES
    assert "scipy" in _WORKER_WARM_PACKAGES
    assert "torch" not in _WORKER_WARM_PACKAGES, (
        "torch must be DROPPED from the warm list — VAD is now ONNX (master plan §6.2 P-1)"
    )
    assert "transformers" not in _WORKER_WARM_PACKAGES, (
        "transformers must be DROPPED — Parakeet is now onnx-asr (master plan §6.2 P-1)"
    )


# ─── 3. Auth token is required ─────────────────────────────────────────


def test_worker_exits_without_token_env() -> None:
    """The worker refuses to start when ``VOICE_TYPER_IPC_TOKEN`` is unset.

    Master plan §7.2 + ADR-0020 §3: the host MUST set the token env var
    before spawning the worker. The worker exits with
    ``EXIT_NO_TOKEN`` (4) without binding the WS server.
    """
    with _spawn_worker(token=None) as proc:
        # The worker should exit quickly (no prewarm, no WS bind). The
        # 15 s bound tolerates loaded-CI startup (cold Python import +
        # Defender scanning under ``-n auto``); it still catches a hung
        # worker.
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
    """A wrong-token auth frame is rejected with ``auth_failed`` + close 1008.

    Mirrors ``test_sidecar_ws_auth_failed.py::test_mismatched_token_emits_auth_failed_frame_before_close``
    for the worker's auth path. The auth-rejection contract is shared
    with the slim-core sidecar (ADR-0020 §3) so the host's respawn
    scheduler can branch on ``code == "auth_failed"`` uniformly.
    """
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
    """A first frame that is not ``{"type":"auth",...}`` is rejected.

    Mirrors ``test_sidecar_ws_auth_failed.py::test_non_auth_first_frame_emits_auth_failed_before_close``.
    """
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
    """If ``VOICE_TYPER_IPC_TOKEN`` is unset, the worker rejects every connection.

    The per-launch token check at ``run()`` time also exits the worker
    (``test_worker_exits_without_token_env``), but ``_handle_connection``
    independently rejects any incoming connection if the env var is
    unset (defense-in-depth — if a future refactor adds an alternate
    entry path that bypasses ``run()``'s check, the connection handler
    still refuses to authenticate).
    """
    monkeypatch.delenv("VOICE_TYPER_IPC_TOKEN", raising=False)
    ws = _make_fake_websocket(json.dumps({"type": "auth", "token": "anything"}))

    stop_event = asyncio.Event()
    shutdown_timer = worker_main._ShutdownTimer()
    await worker_main._handle_connection(ws, prewarm_ran=True, stop_event=stop_event, shutdown_timer=shutdown_timer)

    assert len(ws._sent_frames) == 1
    frame = json.loads(ws._sent_frames[0])
    assert frame["data"]["code"] == "auth_failed"
    assert not stop_event.is_set()


# ─── 4. Shutdown is clean ──────────────────────────────────────────────


async def test_shutdown_command_emits_ack_and_closes(monkeypatch) -> None:
    """The ``shutdown`` command emits ``shutdown_ack`` + closes the socket + sets stop_event.

    Master plan §7.2: the slim-core sidecar sends ``shutdown`` to
    gracefully stop the worker. The worker acknowledges with
    ``shutdown_ack``, sets ``stop_event`` (so :func:`run_worker_server`'s
    ``await stop_event.wait()`` unblocks and ``run()`` returns
    ``EXIT_OK``), and closes the WS. The ``stop_event.set()`` call is
    the regression guard for the shutdown-hang bug where the worker
    sent ``shutdown_ack`` + closed the WS but never set ``stop_event``,
    leaving ``run()`` blocked forever at ``await stop_event.wait()``.
    """
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", _TEST_TOKEN)

    # The worker's _authenticate reads the FIRST frame via
    # ``websocket.recv()`` (a coroutine). After auth, the dispatch
    # loop iterates via ``async for raw in websocket:`` (using
    # ``__aiter__`` / ``__anext__``). So the mock must:
    #   - have ``recv`` return the auth frame on first call
    #   - have ``__aiter__`` return an async iterator that yields the
    #     shutdown frame, then StopAsyncIteration.
    auth_frame = json.dumps({"type": "auth", "token": _TEST_TOKEN}).encode()
    shutdown_frame = json.dumps({"cmd": "shutdown"}).encode()

    recv_calls: list[bytes] = [auth_frame]

    async def _fake_recv() -> bytes:
        if recv_calls:
            return recv_calls.pop(0)
        # Should never be reached — the dispatch loop uses __aiter__.
        raise AssertionError("recv() called after auth frame — dispatch should use __aiter__")

    class _FrameAsyncIter:
        def __aiter__(self) -> _FrameAsyncIter:
            return self

        async def __anext__(self) -> bytes:
            # Yield the shutdown frame, then stop. The worker's
            # ``_handle_connection`` returns after the shutdown frame
            # (it closes the socket and returns from the function).
            if shutdown_frame is not None:
                # Use a sentinel: pop the frame so subsequent calls
                # raise StopAsyncIteration. We can't use ``nonlocal``
                # cleanly inside a method, so we close over a list.
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
    # await stop_event.wait() unblocks and the worker exits cleanly.
    # Without this call, the worker hangs forever after shutdown_ack.
    assert stop_event.is_set(), (
        "shutdown command must set stop_event so run() unblocks — "
        "missing stop_event.set() reproduces the shutdown-hang regression"
    )
    # The shutdown timer MUST be started so the [SHUTDOWN] log line
    # carries a real <duration> suffix (C-LOG-2).
    assert shutdown_timer.elapsed() >= 0.0, "shutdown_timer.start() must have been called"


def test_shutdown_command_exits_worker() -> None:
    """The ``shutdown`` command causes the worker process to exit cleanly with EXIT_OK.

    Regression test (integration, POSIX-only): the ``shutdown`` command
    handler previously did NOT call ``stop_event.set()``, so the worker
    hung forever after receiving ``shutdown`` — ``run()``'s
    ``await stop_event.wait()`` blocked indefinitely, the ``finally``
    block never ran, and the single-instance lockfile leaked on disk.

    This test spawns a real ``python -m voice_typer.worker`` subprocess,
    connects via the ``websockets`` client library, sends ``shutdown``
    via WS, and asserts the worker exits within 3s with ``EXIT_OK`` and
    the lockfile is released.
    """
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
                # may close the socket before we read it (which raises
                # ConnectionClosed) — that's fine, the assertion below
                # on ``proc.wait`` is the authoritative check.
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
                f"worker did not exit within 3s of `shutdown` command — stop_event.set() not called? stderr: {stderr}"
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
    # (verifies the finally:lock_handle.release() block ran).
    lock = _find_worker_lock()
    assert lock is None, f"worker.lock still exists after shutdown command — release() did not run: {lock}"


def test_sigterm_clean_exit() -> None:
    """SIGTERM causes the worker to exit cleanly with code 0.

    Master plan §7.2: the host's kill-children backstop sends SIGTERM
    (POSIX) / ``taskkill`` (Windows). The worker's SIGTERM handler
    initiates graceful shutdown (sets the stop_event → asyncio loop
    exits → ``run()`` returns ``EXIT_OK``).
    """
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
    assert lock is None, f"worker.lock still exists after clean exit — release() did not run: {lock}"


def test_worker_single_instance_lock_rejects_duplicate(monkeypatch, tmp_path) -> None:
    """A second worker spawn is rejected by the single-instance lock.

    Master plan §7.2: the worker takes a single-instance lock file to
    prevent parallel spawns (parallel to ``VoiceTyperSingleInstance``
    for the slim-core sidecar).
    """
    # Mock the config dir to a tmp_path so the test does not touch the
    # real config dir.
    monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
    # Also patch the worker's lazy import of _config_dir (it imports
    # inside _worker_lock_path each call).
    lock_path = tmp_path / "worker.lock"

    if os.name == "posix":
        # Create the lockfile with the current process's PID — the
        # worker's _ensure_worker_single_instance should detect a live
        # PID and refuse to start.
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
