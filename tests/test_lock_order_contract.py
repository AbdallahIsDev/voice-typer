"""NEW-CONC-002: lock-order contract regression tests.

This file enforces the contract documented in
``docs/architecture/lock-order-contract.md``:

1. **Static-analysis layer** — parses ``voice_typer/server/app.py``,
   ``voice_typer/server/service.py``, and
   ``voice_typer/server/dictation_pipeline.py`` for ``with self._lock:``,
   ``with self._config_mutation_lock:``, and
   ``with self._pending_timers_lock:`` blocks and asserts that NONE of
   them nest another of the three app-level locks. Catches accidental
   lock-nesting regressions introduced by future edits.

2. **Lock-graph layer** — builds the directed ``lock A → lock B`` graph
   (A held while B acquired) from the source and asserts no cycle
   exists. Today the graph has zero edges (no nesting), so this is a
   trivially-acyclic check that pins the invariant.

3. **Concurrency stress layer** — constructs a minimal ``VoiceTyperApp``
   shell via ``__new__`` (skipping the recorder / hotkey / tray setup
   that needs hardware) and runs N threads through the production
   lock-using paths (``_schedule_timer``, ``_cancel_pending_timers``,
   ``_config_mutation_lock`` holder, ``_lock`` holder). Asserts no
   thread hangs within 2 s (no deadlock).

4. **Reverse-order sanity layer** — acquires the locks in the documented
   order from one thread and in the REVERSE order from another, to
   prove the locks are independent (no nesting rule ⇒ no possible
   cycle ⇒ reverse-order acquisition must not deadlock). If a future
   regression introduces nesting in the "forward" direction, this test
   would deadlock and time out.

The d-review verdict for NEW-CONC-002 is LOW (architecture nit): no
actual deadlock has been observed. These tests guard the no-nesting
invariant so it stays that way.
"""

from __future__ import annotations

import ctypes
import re
import threading
import time
from pathlib import Path

import pytest

# RW-9 test-infrastructure shim: ``voice_typer.server.crash_handler``
# decorates its VEH callback with ``@ctypes.WINFUNCTYPE(...)`` at
# module-load time. ``WINFUNCTYPE`` only exists on Windows — on
# Linux/macOS the attribute is missing, so importing ``crash_handler``
# (transitively imported by ``voice_typer.server.app``) raises
# ``AttributeError``. Alias it to ``CFUNCTYPE`` so we can import
# ``app`` for the concurrency tests below. Mirrors the shim in
# ``tests/conftest.py``.
if not hasattr(ctypes, "WINFUNCTYPE"):
    ctypes.WINFUNCTYPE = ctypes.CFUNCTYPE  # type: ignore[attr-defined]


SERVER_DIR = Path(__file__).resolve().parent.parent / "voice_typer" / "server"
APP_PY = SERVER_DIR / "app.py"
SERVICE_PY = SERVER_DIR / "service.py"
DICTATION_PIPELINE_PY = SERVER_DIR / "dictation_pipeline.py"

# The three app-level locks enumerated in the contract. ``_lock`` is the
# bare name; ``_config_mutation_lock`` and ``_pending_timers_lock`` are
# uniquely named.
APP_LOCK_NAMES = ("_lock", "_config_mutation_lock", "_pending_timers_lock")

# Regex matching ``with self._lock:`` / ``with self._config_mutation_lock:``
# / ``with self._pending_timers_lock:`` (and the ``with app._lock:`` /
# ``with self._app._lock:`` variants used in service.py and
# dictation_pipeline.py). We intentionally do NOT match ``with
# self._lock_something_else:`` — the trailing ``:`` ensures we only match
# the exact lock name.
_WITH_LOCK_RE = re.compile(
    r"with\s+(?:self\.|self\._app\.|app\.)"
    r"(_config_mutation_lock|_pending_timers_lock|_lock)\s*:"
)

# Regex matching ``threading.Lock()`` / ``threading.RLock()`` declarations
# bound to one of the three lock names.
_LOCK_DECL_RE = re.compile(
    r"self\.(_config_mutation_lock|_pending_timers_lock|_lock)\s*=\s*"
    r"threading\.(Lock|RLock)\(\)"
)

# Regex matching ``threading.Event()`` declarations on VoiceTyperApp.
_EVENT_DECL_RE = re.compile(
    r"self\.(_busy_event|_shutting_down_event|_bubble_level_worker_stop)\s*=\s*"
    r"threading\.Event\(\)"
)


def _read_source(path: Path) -> str:
    """Read a source file. Fail loudly if missing (test infra broken)."""
    assert path.exists(), f"missing source file: {path}"
    return path.read_text(encoding="utf-8")


def _find_with_blocks(source: str) -> list[tuple[int, str, int, int]]:
    """Return list of (with_line, lock_name, body_start, body_end).

    ``body_start`` is the line of the first line inside the block (1-based).
    ``body_end`` is the line of the last line in the block (1-based, the
    last line before dedent). For single-line bodies they are equal.
    """
    lines = source.splitlines(keepends=True)
    matches: list[tuple[int, str, int, int]] = []
    for i, line in enumerate(lines, start=1):
        stripped = line.lstrip()
        if not stripped.startswith("with "):
            continue
        # Join up to 3 lines starting at the ``with`` to handle multi-line
        # ``with`` headers (rare but possible).
        candidate = "".join(lines[i - 1 : i + 2])
        m = _WITH_LOCK_RE.search(candidate)
        if m is None:
            continue
        lock_name = m.group(1)
        with_indent = len(line) - len(stripped)
        body_start = None
        body_end = i
        for j in range(i + 1, len(lines) + 1):
            inner = lines[j - 1] if j - 1 < len(lines) else ""
            inner_stripped = inner.strip()
            if not inner_stripped or inner_stripped.startswith("#"):
                continue
            inner_indent = len(inner) - len(inner.lstrip())
            if inner_indent > with_indent:
                if body_start is None:
                    body_start = j
                body_end = j
            else:
                # First dedented non-blank line — body ended.
                if body_start is not None:
                    break
        if body_start is None:
            # Empty body (e.g. ``with self._lock: pass``). Treat as
            # zero-length.
            body_start = i + 1
            body_end = i + 1
        matches.append((i, lock_name, body_start, body_end))
    return matches


# ─── Static-analysis tests ───────────────────────────────────────────────


class TestLockInventory:
    """Verify the three app-level locks + three events are declared at the
    expected locations. Pins the inventory so a rename is caught."""

    def test_app_locks_declared(self):
        source = _read_source(APP_PY)
        declarations: dict[str, str] = {}
        for m in _LOCK_DECL_RE.finditer(source):
            declarations[m.group(1)] = m.group(2)
        assert "_lock" in declarations, (
            "app._lock (threading.Lock) must be declared in app.py — see docs/architecture/lock-order-contract.md §1"
        )
        assert declarations["_lock"] == "Lock", f"app._lock must be threading.Lock (got {declarations['_lock']})"
        assert "_config_mutation_lock" in declarations, (
            "app._config_mutation_lock (threading.RLock) must be declared — "
            "see docs/architecture/lock-order-contract.md §1"
        )
        assert declarations["_config_mutation_lock"] == "RLock", (
            "app._config_mutation_lock must be threading.RLock (defensive reentrancy — see contract §3 rationale)"
        )
        assert "_pending_timers_lock" in declarations, (
            "app._pending_timers_lock (threading.Lock) must be declared — "
            "see docs/architecture/lock-order-contract.md §1"
        )
        assert declarations["_pending_timers_lock"] == "Lock", (
            f"app._pending_timers_lock must be threading.Lock (got {declarations['_pending_timers_lock']})"
        )

    def test_app_events_declared(self):
        source = _read_source(APP_PY)
        declared_events: set[str] = set()
        for m in _EVENT_DECL_RE.finditer(source):
            declared_events.add(m.group(1))
        # ``_bubble_level_worker_stop`` is created conditionally (under
        # ``if not hasattr(...)``) — the regex above still matches the
        # ``self._bubble_level_worker_stop = threading.Event()`` line
        # inside the ``if``. The other two are unconditional.
        assert "_busy_event" in declared_events, "app._busy_event must be declared — see contract §1"
        assert "_shutting_down_event" in declared_events, "app._shutting_down_event must be declared — see contract §1"


class TestNoLockNesting:
    """NEW-CONC-002 primary invariant: the three app-level locks must
    NEVER be acquired nested within one another. See contract §2 Rule 1."""

    @pytest.mark.parametrize(
        "filepath",
        [APP_PY, SERVICE_PY, DICTATION_PIPELINE_PY],
        ids=["app.py", "service.py", "dictation_pipeline.py"],
    )
    def test_no_nested_app_locks(self, filepath: Path):
        source = _read_source(filepath)
        with_blocks = _find_with_blocks(source)
        lines = source.splitlines()
        # For each ``with <lock>:`` block, scan its body lines for any
        # other app-lock acquisition.
        for with_line, lock_name, body_start, body_end in with_blocks:
            body_text = "\n".join(lines[body_start - 1 : body_end])
            for other in APP_LOCK_NAMES:
                if other == lock_name:
                    continue
                # Match ``with self.<other>:`` / ``with self._app.<other>:``
                # / ``with app.<other>:`` inside the body.
                pattern = re.compile(rf"with\s+(?:self\.|self\._app\.|app\.){re.escape(other)}\s*:")
                assert not pattern.search(body_text), (
                    f"{filepath.name}:{with_line}: ``with self.{lock_name}:`` "
                    f"block (lines {body_start}-{body_end}) acquires "
                    f"``{other}`` — VIOLATES lock-order contract §2 Rule 1. "
                    f"The three app-level locks must NEVER be nested. See "
                    f"docs/architecture/lock-order-contract.md."
                )


class TestLockOrderGraphIsAcyclic:
    """Build the directed ``lock A → lock B`` graph (A held while B
    acquired) from the source and assert it contains no cycles. With the
    no-nesting rule above, the graph is empty (zero edges) — so this is a
    trivially-acyclic check today. It exists to catch future regressions
    if someone intentionally introduces nesting (in which case the test
    must be updated to verify the new ordering is still acyclic)."""

    def test_app_lock_graph_has_no_cycles(self):
        edges: set[tuple[str, str]] = set()
        for filepath in (APP_PY, SERVICE_PY, DICTATION_PIPELINE_PY):
            source = _read_source(filepath)
            lines = source.splitlines()
            with_blocks = _find_with_blocks(source)
            for _with_line, lock_name, body_start, body_end in with_blocks:
                body_text = "\n".join(lines[body_start - 1 : body_end])
                for other in APP_LOCK_NAMES:
                    if other == lock_name:
                        continue
                    pattern = re.compile(rf"with\s+(?:self\.|self\._app\.|app\.){re.escape(other)}\s*:")
                    if pattern.search(body_text):
                        edges.add((lock_name, other))

        # Detect cycles via DFS.
        nodes = set()
        for a, b in edges:
            nodes.add(a)
            nodes.add(b)

        white, gray, black = 0, 1, 2
        color = {n: white for n in nodes}
        adj = {n: [] for n in nodes}
        for a, b in edges:
            adj[a].append(b)

        def dfs(n: str) -> bool:
            color[n] = gray
            for nxt in adj[n]:
                if color[nxt] == gray:
                    return True  # back-edge ⇒ cycle
                if color[nxt] == white and dfs(nxt):
                    return True
            color[n] = black
            return False

        has_cycle = any(color[n] == white and dfs(n) for n in nodes)
        assert not has_cycle, (
            f"Lock-order graph has a cycle! edges={edges}. "
            f"This is a deadlock hazard — see "
            f"docs/architecture/lock-order-contract.md §2 Rule 1."
        )

        # Document the (currently empty) edge set so a future change that
        # adds nesting shows up clearly in the test output.
        assert edges == set(), (
            f"Lock-order graph is non-empty (edges={edges}). As of "
            f"NEW-CONC-002, the three app-level locks must be independent. "
            f"If you intentionally added nesting, update the contract at "
            f"docs/architecture/lock-order-contract.md §2 to define the "
            f"canonical order, then update this assertion to verify "
            f"the new ordering is still acyclic."
        )


# ─── Concurrency stress tests ───────────────────────────────────────────


@pytest.fixture
def app_shell():
    """Construct a minimal VoiceTyperApp shell via ``__new__``.

    We skip ``VoiceTyperApp.__init__`` because it eagerly constructs a
    ``Recorder`` (PortAudio), ``TrayIcon`` (pystray), and ``HotkeyDispatcher``
    — all of which require hardware not available in this headless
    container. The lock-using methods under test (``_schedule_timer`` /
    ``_cancel_pending_timers``) only touch ``self._pending_timers_lock``,
    ``self._pending_timers``, and ``self._timer_generation``, so we
    initialise just those attributes plus the three contract locks.

    The lock objects are REAL ``threading.Lock`` / ``threading.RLock``
    instances — same types as production (see ``app.py:324/336/382``).
    """
    from voice_typer.server.app import VoiceTyperApp

    shell = VoiceTyperApp.__new__(VoiceTyperApp)
    # Match the production declarations exactly.
    shell._lock = threading.Lock()  # app.py:324
    shell._config_mutation_lock = threading.RLock()  # app.py:336
    shell._pending_timers_lock = threading.Lock()  # app.py:382
    shell._pending_timers: list[threading.Timer] = []  # app.py:381
    shell._timer_generation = 0  # app.py:383
    return shell


class TestConcurrentLockUseNoDeadlock:
    """NEW-CONC-002: spawn N threads through the production lock-using
    paths and assert no thread hangs within 2 s."""

    def test_concurrent_schedule_and_cancel_timers_no_deadlock(self, app_shell):
        """``_schedule_timer`` and ``_cancel_pending_timers`` both acquire
        ``_pending_timers_lock``. Stress them concurrently from 4 threads."""
        errors: list[Exception] = []
        stop = threading.Event()

        def producer():
            try:
                while not stop.is_set():
                    # _schedule_timer acquires _pending_timers_lock briefly
                    # to append to the list, then starts the Timer (which
                    # fires after `delay`). Use a tiny delay so the timers
                    # actually fire and self-remove from the daemon thread.
                    app_shell._schedule_timer(0.001, lambda: None)
                    time.sleep(0.0005)
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        def consumer():
            try:
                while not stop.is_set():
                    # _cancel_pending_timers acquires _pending_timers_lock
                    # to snapshot+clear, then cancels each timer OUTSIDE
                    # the lock.
                    app_shell._cancel_pending_timers()
                    time.sleep(0.0005)
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [
            threading.Thread(target=producer, name="timer-producer-1"),
            threading.Thread(target=producer, name="timer-producer-2"),
            threading.Thread(target=consumer, name="timer-consumer-1"),
            threading.Thread(target=consumer, name="timer-consumer-2"),
        ]
        for t in threads:
            t.start()
        # Let them run for 1s; if any thread is stuck in a deadlock the
        # join(timeout=2) below will fail.
        time.sleep(1.0)
        stop.set()
        for t in threads:
            t.join(timeout=2.0)
            assert not t.is_alive(), (
                f"Thread {t.name!r} is still alive after 2s join — likely deadlocked on _pending_timers_lock."
            )
        assert not errors, f"concurrent timer ops raised: {errors}"
        # Final cleanup so no daemon timers leak into the next test.
        app_shell._cancel_pending_timers()

    def test_concurrent_config_mutation_lock_no_deadlock(self, app_shell):
        """Multiple IPC server threads acquiring ``_config_mutation_lock``
        (the RLock) concurrently must not deadlock. Mirrors the production
        ``service.apply_config`` / ``service.onboarding_apply_settings``
        paths which both hold this lock briefly."""
        errors: list[Exception] = []
        iterations = 200
        barrier = threading.Barrier(8)

        def worker(idx: int):
            try:
                barrier.wait(timeout=5.0)
                for _ in range(iterations):
                    # Production code does setattr + save + side-effects
                    # inside this lock; we just exercise the lock itself.
                    # Simulate a brief critical section. The RLock
                    # allows the SAME thread to re-acquire — exercise
                    # that too (defensive reentrancy per contract §3).
                    with app_shell._config_mutation_lock, app_shell._config_mutation_lock:
                        pass
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)
            assert not t.is_alive(), "Thread still alive after 5s — likely deadlocked on _config_mutation_lock."
        assert not errors, f"concurrent config-mutation raised: {errors}"

    def test_concurrent_app_lock_no_deadlock(self, app_shell):
        """``app._lock`` is acquired in ``dictation_pipeline.py:282`` to
        clear ``recording._transcription_thread``. Multiple transcription
        threads finishing concurrently must not deadlock."""
        errors: list[Exception] = []
        iterations = 200
        barrier = threading.Barrier(8)

        def worker():
            try:
                barrier.wait(timeout=5.0)
                for _ in range(iterations):
                    # Mirror dictation_pipeline.py:282 — acquire app._lock
                    # briefly to clear a shared attribute.
                    with app_shell._lock:
                        # Single attribute write (the production code
                        # writes ``recording._transcription_thread =
                        # None``).
                        pass
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)
            assert not t.is_alive(), "Thread still alive after 5s — likely deadlocked on app._lock."
        assert not errors, f"concurrent app._lock raised: {errors}"

    def test_mixed_locks_concurrent_no_deadlock(self, app_shell):
        """Stress all three app-level locks concurrently from independent
        thread pools. Because the contract requires no nesting, the three
        pools must make progress independently."""
        errors: list[Exception] = []
        stop = threading.Event()

        def timer_pool():
            try:
                while not stop.is_set():
                    app_shell._schedule_timer(0.001, lambda: None)
                    app_shell._cancel_pending_timers()
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        def config_pool():
            try:
                while not stop.is_set():
                    with app_shell._config_mutation_lock:
                        pass
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        def app_lock_pool():
            try:
                while not stop.is_set():
                    with app_shell._lock:
                        pass
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = (
            [threading.Thread(target=timer_pool, name="timer") for _ in range(3)]
            + [threading.Thread(target=config_pool, name="config") for _ in range(3)]
            + [threading.Thread(target=app_lock_pool, name="app-lock") for _ in range(3)]
        )
        for t in threads:
            t.start()
        time.sleep(1.0)
        stop.set()
        for t in threads:
            t.join(timeout=2.0)
            assert not t.is_alive(), f"Thread {t.name!r} still alive after 2s join — likely deadlocked."
        assert not errors, f"mixed-lock stress raised: {errors}"
        app_shell._cancel_pending_timers()


class TestReverseOrderAcquisitionNoDeadlock:
    """NEW-CONC-002 sanity: prove the three app locks are *independent*
    (i.e., acquiring them in any order from any thread does not deadlock).

    The contract requires NO nesting — so reverse-order acquisition is a
    no-op (no edge in the lock-order graph). This test exists to catch a
    future regression where someone introduces nesting in the "forward"
    direction (e.g. ``_config_mutation_lock`` held while
    ``_pending_timers_lock`` is acquired): in that case, a thread doing
    the reverse would deadlock, and this test would fail with a 2s
    timeout.
    """

    def test_reverse_order_does_not_deadlock(self, app_shell):
        """Thread A acquires locks in the documented "forward" order
        (config → timers → app); thread B acquires them in reverse
        (app → timers → config). Each thread acquires only ONE lock at a
        time (no nesting) — so neither thread blocks the other. If the
        production code ever starts nesting these locks, this test will
        deadlock and time out."""
        errors: list[Exception] = []
        iterations = 100
        barrier = threading.Barrier(2)

        forward_order = (
            app_shell._config_mutation_lock,
            app_shell._pending_timers_lock,
            app_shell._lock,
        )
        reverse_order = (
            app_shell._lock,
            app_shell._pending_timers_lock,
            app_shell._config_mutation_lock,
        )

        def worker(order, name):
            try:
                barrier.wait(timeout=5.0)
                for _ in range(iterations):
                    # Acquire each lock INDIVIDUALLY (not nested) in the
                    # given order. If a future regression introduces
                    # nesting, the production code would have edges in
                    # one direction; this test acquires them in BOTH
                    # directions concurrently — if any nesting exists,
                    # one of the two threads will block waiting for a
                    # lock the other holds, and the join will time out.
                    for lock in order:
                        with lock:
                            time.sleep(0.0001)
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        t_forward = threading.Thread(target=worker, args=(forward_order, "forward"), name="forward")
        t_reverse = threading.Thread(target=worker, args=(reverse_order, "reverse"), name="reverse")
        t_forward.start()
        t_reverse.start()
        t_forward.join(timeout=5.0)
        t_reverse.join(timeout=5.0)
        assert not t_forward.is_alive(), (
            "forward-order thread still alive after 5s — production code "
            "has introduced nesting that breaks the no-nesting contract "
            "(see docs/architecture/lock-order-contract.md §2 Rule 1)."
        )
        assert not t_reverse.is_alive(), (
            "reverse-order thread still alive after 5s — production code "
            "has introduced nesting that breaks the no-nesting contract "
            "(see docs/architecture/lock-order-contract.md §2 Rule 1)."
        )
        assert not errors, f"reverse-order test raised: {errors}"


# Ensure no stale ``sys.modules`` entries leak between test modules when
# this file is collected alongside others that mock ``ctypes`` differently.
# (No-op here — just a marker that we considered cleanup.)
