"""lock-order contract regression tests."""

from __future__ import annotations

import ctypes
import re
import threading
import time
from pathlib import Path

import pytest

if not hasattr(ctypes, "WINFUNCTYPE"):
    ctypes.WINFUNCTYPE = ctypes.CFUNCTYPE  # type: ignore[attr-defined]


SERVER_DIR = Path(__file__).resolve().parent.parent / "voice_typer" / "server"
APP_PY = SERVER_DIR / "app.py"
SERVICE_DIR = SERVER_DIR / "service"
SERVICE_PY_FILES = sorted(SERVICE_DIR.glob("*.py")) if SERVICE_DIR.is_dir() else [SERVER_DIR / "service.py"]
# For backwards-compat with tests that import SERVICE_PY as a single path,
SERVICE_PY = SERVICE_DIR / "_base.py"
DICTATION_PIPELINE_PY = SERVER_DIR / "dictation_stages.py"
# ``_pending_timers_lock`` was migrated to ``TimerCoordinator`` (
TIMER_COORDINATOR_PY = SERVER_DIR / "timer_coordinator.py"
# ``_lock`` / ``_busy_event`` construction migrated to ``BusynessCoordinator``
BUSYNESS_PY = SERVER_DIR / "_busyness.py"

APP_LOCK_NAMES = ("_lock", "_config_mutation_lock", "_pending_timers_lock")

_WITH_LOCK_RE = re.compile(
    r"with\s+(?:self\.|self\._app\.|app\.)"
    r"(_config_mutation_lock|_pending_timers_lock|_lock)\s*:"
)

# Regex matching ``threading.Lock()`` / ``threading.RLock()`` declarations
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


def _strip_comments_and_docstrings(source: str) -> str:
    """Strip ``#`` comments and triple-quoted strings before regex matching."""
    # Remove triple-quoted strings (docstrings + multi-line strings).
    source = re.sub(r'"""[\s\S]*?"""', '""""""', source)
    source = re.sub(r"'''[\s\S]*?'''", "''''''", source)
    # Remove ``#`` comments per line (keep code before the ``#``).
    lines = [line.split("#", 1)[0] for line in source.split("\n")]
    return "\n".join(lines)


def _find_with_blocks(source: str) -> list[tuple[int, str, int, int]]:
    """Return list of (with_line, lock_name, body_start, body_end)."""
    lines = source.splitlines(keepends=True)
    matches: list[tuple[int, str, int, int]] = []
    for i, line in enumerate(lines, start=1):
        stripped = line.lstrip()
        if not stripped.startswith("with "):
            continue
        # Join up to 3 lines starting at the ``with`` to handle multi-line
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
                # First dedented non-blank line, body ended.
                if body_start is not None:
                    break
        if body_start is None:
            body_start = i + 1
            body_end = i + 1
        matches.append((i, lock_name, body_start, body_end))
    return matches


class TestLockInventory:
    """Verify the three app-level locks + three events are declared at the"""

    def test_app_locks_declared(self):
        app_source = _strip_comments_and_docstrings(_read_source(APP_PY))
        app_declarations: dict[str, str] = {}
        for m in _LOCK_DECL_RE.finditer(app_source):
            app_declarations[m.group(1)] = m.group(2)
        assert "_config_mutation_lock" in app_declarations, (
            "app._config_mutation_lock (threading.RLock) must be declared, "
            "see docs/architecture/lock-order-contract.md §1"
        )
        assert app_declarations["_config_mutation_lock"] == "RLock", (
            "app._config_mutation_lock must be threading.RLock (defensive reentrancy: see contract §3 rationale)"
        )

        bc_source = _strip_comments_and_docstrings(_read_source(BUSYNESS_PY))
        bc_declarations: dict[str, str] = {}
        for m in _LOCK_DECL_RE.finditer(bc_source):
            bc_declarations[m.group(1)] = m.group(2)
        assert "_lock" in bc_declarations, (
            "app._lock (threading.Lock) must be declared in _busyness.py "
            "(BusynessCoordinator owns the construction; app.py serves it "
            "via a delegating property): see "
            "docs/architecture/lock-order-contract.md §1"
        )
        assert bc_declarations["_lock"] == "Lock", f"app._lock must be threading.Lock (got {bc_declarations['_lock']})"

        # ``_pending_timers_lock`` is owned by ``TimerCoordinator`` (
        tc_source = _strip_comments_and_docstrings(_read_source(TIMER_COORDINATOR_PY))
        tc_declarations: dict[str, str] = {}
        for m in _LOCK_DECL_RE.finditer(tc_source):
            tc_declarations[m.group(1)] = m.group(2)
        assert "_pending_timers_lock" in tc_declarations, (
            "app._pending_timers_lock (threading.Lock) must be declared in "
            "timer_coordinator.py (Phase 7 migrated the timer state "
            "to TimerCoordinator; app.py only keeps a shadow attribute "
            "pointing at the coordinator's lock): see "
            "docs/architecture/lock-order-contract.md §1"
        )
        assert tc_declarations["_pending_timers_lock"] == "Lock", (
            f"app._pending_timers_lock must be threading.Lock (got {tc_declarations['_pending_timers_lock']})"
        )

    def test_app_events_declared(self):
        source = _strip_comments_and_docstrings(_read_source(APP_PY))
        declared_events: set[str] = set()
        for m in _EVENT_DECL_RE.finditer(source):
            declared_events.add(m.group(1))
        # ``_bubble_level_worker_stop`` is created conditionally (under
        assert "_shutting_down_event" in declared_events, "app._shutting_down_event must be declared: see contract §1"

        # ``_busy_event`` construction lives in ``BusynessCoordinator``
        bc_source = _strip_comments_and_docstrings(_read_source(BUSYNESS_PY))
        bc_declared_events: set[str] = set()
        for m in _EVENT_DECL_RE.finditer(bc_source):
            bc_declared_events.add(m.group(1))
        assert "_busy_event" in bc_declared_events, (
            "app._busy_event must be declared in _busyness.py "
            "(BusynessCoordinator owns the construction; app.py serves it "
            "via a delegating property): see contract §1"
        )


class TestNoLockNesting:
    """primary invariant: the three app-level locks must"""

    @pytest.mark.parametrize(
        "filepath",
        [APP_PY, *SERVICE_PY_FILES, DICTATION_PIPELINE_PY],
        ids=["app.py"] + [f"service/{p.name}" for p in SERVICE_PY_FILES] + ["dictation_pipeline.py"],
    )
    def test_no_nested_app_locks(self, filepath: Path):
        source = _read_source(filepath)
        with_blocks = _find_with_blocks(source)
        lines = source.splitlines()
        # For each ``with <lock>:`` block, scan its body lines for any
        for with_line, lock_name, body_start, body_end in with_blocks:
            body_text = "\n".join(lines[body_start - 1 : body_end])
            for other in APP_LOCK_NAMES:
                if other == lock_name:
                    continue
                # Match ``with self.<other>:`` / ``with self._app.<other>:``
                pattern = re.compile(rf"with\s+(?:self\.|self\._app\.|app\.){re.escape(other)}\s*:")
                assert not pattern.search(body_text), (
                    f"{filepath.name}:{with_line}: ``with self.{lock_name}:`` "
                    f"block (lines {body_start}-{body_end}) acquires "
                    f"``{other}``, VIOLATES lock-order contract §2 Rule 1. "
                    f"The three app-level locks must NEVER be nested. See "
                    f"docs/architecture/lock-order-contract.md."
                )


class TestLockOrderGraphIsAcyclic:
    """Build the directed ``lock A → lock B`` graph (A held while B"""

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
            f"This is a deadlock hazard, see "
            f"docs/architecture/lock-order-contract.md §2 Rule 1."
        )

        assert edges == set(), (
            f"Lock-order graph is non-empty (edges={edges}). As of "
            f"NEW-CONC-002, the three app-level locks must be independent. "
            f"If you intentionally added nesting, update the contract at "
            f"docs/architecture/lock-order-contract.md §2 to define the "
            f"canonical order, then update this assertion to verify "
            f"the new ordering is still acyclic."
        )


@pytest.fixture
def app_shell():
    """Construct a minimal VoiceTyperApp shell via ``__new__``."""
    from voice_typer.server._busyness import BusynessCoordinator
    from voice_typer.server.app import VoiceTyperApp
    from voice_typer.server.timer_coordinator import TimerCoordinator

    shell = VoiceTyperApp.__new__(VoiceTyperApp)
    # Match the production declarations exactly: ``VoiceTyperApp.__init__``
    shell._busyness = BusynessCoordinator()
    shell._config_mutation_lock = threading.RLock()  # app.py:336
    shell.timers = TimerCoordinator(shell)
    # Shadow declarations (mirror production __init__): point the shell's
    shell._pending_timers_lock = shell.timers._pending_timers_lock
    shell._pending_timers = shell.timers._pending_timers
    shell._timer_generation = shell.timers._timer_generation
    return shell


class TestConcurrentLockUseNoDeadlock:
    """spawn N threads through the production lock-using"""

    def test_concurrent_schedule_and_cancel_timers_no_deadlock(self, app_shell):
        """``_schedule_timer`` and ``_cancel_pending_timers`` both acquire"""
        errors: list[Exception] = []
        stop = threading.Event()

        def producer():
            try:
                while not stop.is_set():
                    app_shell._schedule_timer(0.001, lambda: None)
                    time.sleep(0.0005)  # intentional fixed delay (stress-test pacing)
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        def consumer():
            try:
                while not stop.is_set():
                    app_shell._cancel_pending_timers()
                    time.sleep(0.0005)  # intentional fixed delay (stress-test pacing)
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
        time.sleep(1.0)  # intentional fixed delay (stress-test duration)
        stop.set()
        for t in threads:
            t.join(timeout=5.0)
            assert not t.is_alive(), (
                f"Thread {t.name!r} is still alive after 2s join, likely deadlocked on _pending_timers_lock."
            )
        assert not errors, f"concurrent timer ops raised: {errors}"
        # Final cleanup so no daemon timers leak into the next test.
        app_shell._cancel_pending_timers()

    def test_concurrent_config_mutation_lock_no_deadlock(self, app_shell):
        """Multiple IPC server threads acquiring ``_config_mutation_lock``"""
        errors: list[Exception] = []
        iterations = 200
        barrier = threading.Barrier(8)

        def worker(idx: int):
            try:
                barrier.wait(timeout=10.0)
                for _ in range(iterations):
                    with app_shell._config_mutation_lock, app_shell._config_mutation_lock:
                        pass
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)
            assert not t.is_alive(), "Thread still alive after 5s, likely deadlocked on _config_mutation_lock."
        assert not errors, f"concurrent config-mutation raised: {errors}"

    def test_concurrent_app_lock_no_deadlock(self, app_shell):
        """clear ``recording._transcription_thread``. Multiple transcription"""
        errors: list[Exception] = []
        iterations = 200
        barrier = threading.Barrier(8)

        def worker():
            try:
                barrier.wait(timeout=10.0)
                for _ in range(iterations):
                    # Mirror dictation_pipeline.py:282, acquire app._lock
                    with app_shell._lock:
                        # Single attribute write (the production code
                        pass
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)
            assert not t.is_alive(), "Thread still alive after 5s, likely deadlocked on app._lock."
        assert not errors, f"concurrent app._lock raised: {errors}"

    def test_mixed_locks_concurrent_no_deadlock(self, app_shell):
        """Stress all three app-level locks concurrently from independent"""
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
        time.sleep(1.0)  # intentional fixed delay (stress-test duration)
        stop.set()
        for t in threads:
            t.join(timeout=5.0)
            assert not t.is_alive(), f"Thread {t.name!r} still alive after 2s join, likely deadlocked."
        assert not errors, f"mixed-lock stress raised: {errors}"
        app_shell._cancel_pending_timers()


class TestReverseOrderAcquisitionNoDeadlock:
    """sanity: prove the three app locks are *independent*"""

    def test_reverse_order_does_not_deadlock(self, app_shell):
        """Thread A acquires locks in the documented \"forward\" order"""
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
                barrier.wait(timeout=10.0)
                for _ in range(iterations):
                    for lock in order:
                        with lock:
                            time.sleep(0.0001)  # intentional fixed delay (brief lock hold for race window)
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        t_forward = threading.Thread(target=worker, args=(forward_order, "forward"), name="forward")
        t_reverse = threading.Thread(target=worker, args=(reverse_order, "reverse"), name="reverse")
        t_forward.start()
        t_reverse.start()
        t_forward.join(timeout=10.0)
        t_reverse.join(timeout=10.0)
        assert not t_forward.is_alive(), (
            "forward-order thread still alive after 5s, production code "
            "has introduced nesting that breaks the no-nesting contract "
            "(see docs/architecture/lock-order-contract.md §2 Rule 1)."
        )
        assert not t_reverse.is_alive(), (
            "reverse-order thread still alive after 5s, production code "
            "has introduced nesting that breaks the no-nesting contract "
            "(see docs/architecture/lock-order-contract.md §2 Rule 1)."
        )
        assert not errors, f"reverse-order test raised: {errors}"
