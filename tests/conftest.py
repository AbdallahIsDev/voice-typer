"""Shared autouse fixture: mock heavy imports so all tests run headless."""

import ctypes
import sys
import time
import warnings
from unittest.mock import MagicMock

import pytest

from tests.fixtures.cache_resets import clear_caches

# ── Collection-time heavy-import mocks ──
_mock_pystray_collection = MagicMock()
_mock_pystray_collection.Menu = MagicMock
_mock_pystray_collection.Menu.SEPARATOR = "SEP"
_mock_pystray_collection.MenuItem = MagicMock
_mock_pystray_collection.Icon = MagicMock
sys.modules.setdefault("pystray", _mock_pystray_collection)
sys.modules.setdefault("pynput", MagicMock(name="collection_pynput"))
sys.modules.setdefault("pynput.keyboard", MagicMock(name="collection_pynput_keyboard"))
sys.modules.setdefault("pyperclip", MagicMock(name="collection_pyperclip"))


class MockHeavyImportsWarning(UserWarning):
    """Emitted by ``mock_heavy_imports`` when a patch fails."""


_mock_heavy_imports_warned: dict[str, bool] = {}


def _warn_once(kind: str, message: str) -> None:
    """Emit a :class:`MockHeavyImportsWarning` at most once per session."""
    if _mock_heavy_imports_warned.get(kind):
        return
    _mock_heavy_imports_warned[kind] = True
    warnings.warn(message, MockHeavyImportsWarning, stacklevel=2)


@pytest.fixture
def accept_mock_http_peer(monkeypatch):
    """Accept a MagicMock socket peer in the SEC peer-IP pin.

    Modules that mock an HTTP response expose a ``MagicMock`` socket, so
    ``verify_peer_ip_allowed`` (the URL-allowlist TOCTOU guard) rejects it
    before the request path under test runs. The guard's own primitives stay
    covered by ``tests/security/test_url_allowlist_toctou.py``; this
    opt-in fixture lets the HTTP-path modules exercise the real call chain.
    Both call-time-resolved seams are patched (LLM polisher + cloud facade).
    """

    def _allow(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr("voice_typer.server._secrets.verify_peer_ip_allowed", _allow)
    monkeypatch.setattr("voice_typer.server.cloud_engines.verify_peer_ip_allowed", _allow)


def pytest_configure(config):
    """register the real_pynput and real_pil markers."""
    config.addinivalue_line(
        "markers",
        "real_pynput: opt out of the pynput mock (use real pynput.keyboard)",
    )
    config.addinivalue_line(
        "markers",
        "real_pil: opt out of the PIL mock (use real PIL for image tests)",
    )
    config.addinivalue_line(
        "markers",
        "slow: marks tests as slow (deselect with '-m \"not slow\"')",
    )
    config.addinivalue_line(
        "markers",
        "real_config_dir: opt out of the per-test user-config-dir "
        "isolation (use the real _config_dir resolution). Only for "
        "tests that assert the resolver's own behavior (caching, "
        "platform paths, env overrides).",
    )

    try:
        from hypothesis import HealthCheck, settings
    except ImportError:
        # ``pytestmark = pytest.mark.skipif(not HAS_HYPOTHESIS, ...)``.
        return

    settings.register_profile(
        "ci",
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
        print_blob=True,
    )
    # ``load_profile`` is idempotent and safe to call multiple times
    settings.load_profile("ci")


def pytest_addoption(parser):
    """add ``--slow`` flag to opt in to slow tests."""
    parser.addoption(
        "--slow",
        action="store_true",
        default=False,
        help="run slow tests (default: skipped)",
    )


def pytest_collection_modifyitems(config, items):
    """skip slow tests unless ``--slow`` was passed."""
    if config.getoption("--slow"):
        return
    skip_slow = pytest.mark.skip(reason="need --slow option to run")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip_slow)


# WAV fixture file ``tests/fixtures/test_440hz_1s_16k.wav``


@pytest.fixture(autouse=True)
def winfunctype_alias(monkeypatch):
    """Provide ``ctypes.WINFUNCTYPE`` on non-Windows platforms."""
    if not hasattr(ctypes, "WINFUNCTYPE"):
        monkeypatch.setattr(
            ctypes,
            "WINFUNCTYPE",
            ctypes.CFUNCTYPE,
            raising=False,
        )


# Originally a single autouse ``mock_heavy_imports`` fixture ran at


@pytest.fixture(scope="session", autouse=True)
def mock_heavy_imports_session():
    """Install unconditional heavy-import mocks once per worker session."""
    mp = pytest.MonkeyPatch()
    try:
        mock_sd = MagicMock()
        mock_sd.query_devices.return_value = []
        mp.setitem(sys.modules, "sounddevice", mock_sd)

        mock_whisper = MagicMock()
        mp.setitem(sys.modules, "faster_whisper", mock_whisper)
        mp.setitem(sys.modules, "faster_whisper.WhisperModel", MagicMock())

        mp.setitem(sys.modules, "pystray", MagicMock())
        mp.setitem(sys.modules, "pyperclip", MagicMock())

        # VAD path), so a global torch stand-in is dead weight. Contract

        # BLOCK THE REAL ``winreg`` MODULE. Setting ``sys.modules["winreg"]``
        mp.setitem(sys.modules, "winreg", None)

        # Never auto-detect a REAL platform volume backend during tests.
        # Skip when third-party deps are absent (bare-pytest guard job):
        # unimportable server_platform means no backend to patch.
        try:
            mp.setattr("voice_typer.server.server_platform.volume_factory.get_volume_backend", lambda: None)
        except ImportError:
            _warn_once(
                "volume_factory",
                "mock_heavy_imports_session: server_platform unimportable "
                "(missing third-party dep); volume-backend patch skipped",
            )

        yield
    finally:
        mp.undo()


@pytest.fixture(autouse=True)
def mock_heavy_imports(monkeypatch, request):
    """Per-test conditional mocks + atexit + keyboard_ownership reset."""
    if not request.node.get_closest_marker("real_pynput"):
        mock_pynput = MagicMock()
        mock_pynput_kb = MagicMock()
        monkeypatch.setitem(sys.modules, "pynput", mock_pynput)
        monkeypatch.setitem(sys.modules, "pynput.keyboard", mock_pynput_kb)
    else:
        for _key in ("pynput", "pynput.keyboard"):
            _existing = sys.modules.get(_key)
            if _existing is not None and getattr(_existing, "__spec__", None) is None:
                del sys.modules[_key]

    if not request.node.get_closest_marker("real_pil"):
        mock_pil = MagicMock()
        monkeypatch.setitem(sys.modules, "PIL", mock_pil)
        monkeypatch.setitem(sys.modules, "PIL.Image", MagicMock())
        monkeypatch.setitem(sys.modules, "PIL.ImageDraw", MagicMock())
    else:
        for _key in ("PIL", "PIL.Image", "PIL.ImageDraw"):
            _existing = sys.modules.get(_key)
            if _existing is not None and getattr(_existing, "__spec__", None) is None:
                # Looks like a mock (or a non-module object), evict it
                del sys.modules[_key]
        try:
            import importlib as _importlib

            _real_pil = _importlib.import_module("PIL")
            _real_pil_image = _importlib.import_module("PIL.Image")
            _real_pil_imagedraw = _importlib.import_module("PIL.ImageDraw")
            monkeypatch.setitem(sys.modules, "PIL", _real_pil)
            monkeypatch.setitem(sys.modules, "PIL.Image", _real_pil_image)
            monkeypatch.setitem(sys.modules, "PIL.ImageDraw", _real_pil_imagedraw)
        except ImportError:
            pass  # PIL not available, tests will skip

    # Prevent atexit handler from polluting test output. :
    try:
        monkeypatch.setattr(
            "atexit.register",
            lambda *a, **kw: None,
        )
    except (ImportError, AttributeError) as exc:
        _warn_once(
            "atexit_register",
            "mock_heavy_imports: could not patch "
            "'atexit.register' "
            f"({type(exc).__name__}: {exc}); atexit handlers may fire "
            "during tests.",
        )

    try:
        from voice_typer.server.hotkeys import PynputHotkey

        def _force_pynput(hotkey_str, role=None, **kwargs):
            return PynputHotkey(hotkey_str)

        monkeypatch.setattr(
            "voice_typer.server.hotkey_dispatcher.create_hotkey_backend",
            _force_pynput,
        )
    except (ImportError, AttributeError) as exc:
        _warn_once(
            "force_pynput_hotkey_backend",
            "mock_heavy_imports: could not hoist "
            "force_pynput_hotkey_backend patch "
            f"({type(exc).__name__}: {exc}); hotkey tests may fail "
            "on non-Linux platforms.",
        )

    # (IMPROVE-mode run, 2026-07-21): reset the keyboard_ownership
    try:
        from voice_typer.server.keyboard_ownership import keyboard_ownership

        keyboard_ownership().reset()
    except (ImportError, AttributeError) as exc:
        _warn_once(
            "keyboard_ownership_reset",
            "mock_heavy_imports: could not reset keyboard_ownership "
            "singleton "
            f"({type(exc).__name__}: {exc}); hotkey ownership state "
            "may leak between tests.",
        )


@pytest.fixture(autouse=True)
def _reset_log_rate_limit():
    """Reset ``log_rate_limit`` module-level state between tests."""
    from voice_typer.server import log_rate_limit

    log_rate_limit.reset()
    yield
    log_rate_limit.reset()


@pytest.fixture(autouse=True)
def _restore_vt_logging_state():
    """Snapshot and restore the ``voice_typer`` logger between tests."""
    import logging

    from voice_typer.server.log import _module_level_overrides

    vt_root = logging.getLogger("voice_typer")
    saved_handlers = list(vt_root.handlers)
    saved_filters = list(vt_root.filters)
    saved_level = vt_root.level
    saved_overrides = dict(_module_level_overrides)
    saved_last_resort_filters = list(logging.lastResort.filters) if logging.lastResort is not None else []

    yield

    # Drop handlers the test installed WITHOUT closing them: a leaked
    vt_root.handlers = saved_handlers
    vt_root.filters = saved_filters
    vt_root.setLevel(saved_level)
    _module_level_overrides.clear()
    _module_level_overrides.update(saved_overrides)
    if logging.lastResort is not None:
        logging.lastResort.filters = saved_last_resort_filters


@pytest.fixture(autouse=True)
def _restore_event_bus_transport_probes():
    """Snapshot + restore ``event_bus._transport_probes`` between tests."""
    from voice_typer.server import event_bus

    # Snapshot under the registry lock: a late weakref eviction
    with event_bus._transport_probes_lock:
        saved = list(event_bus._transport_probes)
    yield
    event_bus._transport_probes = saved


@pytest.fixture(autouse=True)
def _drain_crash_recovery_workers():
    """Stop any leaked ``CrashRecovery`` save worker thread between tests."""
    import contextlib

    yield
    with contextlib.suppress(Exception):
        from voice_typer.server import crash_recovery as _cr

        for inst in list(_cr._LIVE_INSTANCES):
            inst.shutdown()


@pytest.fixture(autouse=True)
def _drain_watchdog_threads():
    """Stop any leaked ``TranscriptionWatchdog`` threads between tests."""
    yield
    import contextlib

    with contextlib.suppress(Exception):
        from voice_typer.server.transcription_watchdog import (
            _LIVE_WATCHDOG_CONTROLLERS,
        )

        for ctrl in list(_LIVE_WATCHDOG_CONTROLLERS):
            ctrl._stop_watchdog_thread()


@pytest.fixture(autouse=True)
def _drain_ptt_safety_timers():
    """Cancel any leaked PTT safety timers between tests."""
    yield
    import contextlib

    with contextlib.suppress(Exception):
        from voice_typer.server.hotkey_dispatcher import (
            _LIVE_PTT_TIMER_DISPATCHERS,
        )

        for dispatcher in list(_LIVE_PTT_TIMER_DISPATCHERS):
            dispatcher._cancel_ptt_safety_timer()


@pytest.fixture(autouse=True)
def _drain_shutdown_watchdogs():
    """Disarm any leaked shutdown-watchdog threads between tests."""
    yield
    import contextlib

    with contextlib.suppress(Exception):
        from voice_typer.server.shutdown.lifecycle import (
            _drain_shutdown_watchdogs as _drain_watchdog_threads_impl,
        )

        _drain_watchdog_threads_impl()


@pytest.fixture(autouse=True)
def _drain_test_thread_registry_workers():
    """Stop and join any live ``test_thread_registry`` worker threads."""
    yield
    import contextlib

    with contextlib.suppress(Exception):
        import tests.test_thread_registry as _ttr

        _ttr._drain_test_workers()


@pytest.fixture(autouse=True)
def _drain_thread_registries():
    """Call ``shutdown_all()`` on every live ``ThreadRegistry``."""
    yield
    import contextlib

    with contextlib.suppress(Exception):
        from voice_typer.server.thread_registry import (
            _drain_live_thread_registries,
        )

        _drain_live_thread_registries()


@pytest.fixture(autouse=True)
def _restore_tauri_sidecar_env():
    """Snapshot + restore ``TAURI_SIDECAR`` between tests."""
    import os

    _sentinel = object()
    saved = os.environ.get("TAURI_SIDECAR", _sentinel)
    yield
    if saved is _sentinel:
        os.environ.pop("TAURI_SIDECAR", None)
    else:
        os.environ["TAURI_SIDECAR"] = saved


@pytest.fixture(scope="session", autouse=True)
def _vt_capture_worker_os_exit():
    """Diagnostic (opt-in): capture ``os._exit()`` calls with tracebacks."""
    import os as _os_mod
    import threading as _threading_mod

    log_dir = _os_mod.environ.get("VT_OSEXIT_LOG")
    if not log_dir:
        yield
        return

    _os_mod.makedirs(log_dir, exist_ok=True)
    pid = _os_mod.getpid()
    session_log = _os_mod.path.join(log_dir, f"session-{pid}.log")
    try:
        with open(session_log, "a", encoding="utf-8") as _fh:
            _fh.write(f"sessionstart pid={pid} thread={_threading_mod.current_thread().name}\n")
    except Exception:
        pass

    import faulthandler as _faulthandler_mod

    _real_exit = _os_mod._exit
    os_exit_log = _os_mod.path.join(log_dir, f"os-exit-{pid}.log")

    def _logged_exit(code: int = 0) -> None:
        try:
            with open(os_exit_log, "a", encoding="utf-8") as _fh:
                _fh.write(
                    f"\n===== os._exit({code}) from thread {_threading_mod.current_thread().name} (pid={pid}) =====\n"
                )
                _faulthandler_mod.dump_traceback(file=_fh)
                # Also dump the live ThreadRegistry contents (names +
                try:
                    from voice_typer.server.thread_registry import (
                        _LIVE_REGISTRIES,
                    )

                    _fh.write("\n--- live ThreadRegistry contents ---\n")
                    for _r in list(_LIVE_REGISTRIES):
                        _entries = list(getattr(_r, "_entries", {}).values())
                        _fh.write(f"registry {_r!r}: {len(_entries)} entries\n")
                        for _e in _entries:
                            _fh.write(
                                f"  {_e.name!r}: alive="
                                f"{_e.thread.is_alive()}, join_timeout="
                                f"{_e.join_timeout!r}, stop_event="
                                f"{_e.stop_event is not None}\n"
                            )
                except Exception:
                    pass
        except Exception:
            pass
        _real_exit(code)

    _os_mod._exit = _logged_exit  # type: ignore[assignment]

    # Periodic faulthandler snapshot (every 40s) so a slow/hung phase is
    _snapshot_stop = _threading_mod.Event()

    def _snapshot_loop() -> None:
        while not _snapshot_stop.wait(40.0):
            try:
                with open(os_exit_log, "a", encoding="utf-8") as _fh:
                    _fh.write(f"\n===== periodic dump (pid={pid}) at t={time.monotonic():.0f}s =====\n")
                    _faulthandler_mod.dump_traceback(file=_fh)
            except Exception:
                pass

    _snapshot_thread = _threading_mod.Thread(
        target=_snapshot_loop,
        name="vt-os-exit-snapshot",
        daemon=True,
    )
    _snapshot_thread.start()

    try:
        yield
    finally:
        _snapshot_stop.set()
        try:
            with open(session_log, "a", encoding="utf-8") as _fh:
                _fh.write(f"sessionfinish pid={pid} thread={_threading_mod.current_thread().name}\n")
        except Exception:
            pass


@pytest.fixture
def tmp_config_dir(tmp_path, monkeypatch):
    """Temporary config directory with ``_config_dir`` monkeypatched."""
    from tests.fixtures.config_helpers import patch_config_dir_refs

    patch_config_dir_refs(monkeypatch, tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def _isolate_user_config_dir(tmp_path, monkeypatch, request):
    """Redirect EVERY test's config dir to a per-test tmp dir."""
    if request.node.get_closest_marker("real_config_dir"):
        yield
        return
    from voice_typer.server.config_internals import paths as _paths_impl

    _real_config_dir = _paths_impl._config_dir
    monkeypatch.setenv("VOICE_TYPER_CONFIG_DIR", str(tmp_path))
    from tests.fixtures.config_helpers import patch_config_dir_refs

    patch_config_dir_refs(monkeypatch, tmp_path)
    _real_config_dir.cache_clear()
    yield
    _real_config_dir.cache_clear()


@pytest.fixture
def history_db(tmp_path, monkeypatch):
    """Temporary HistoryDB backed by a SQLite file in tmp_path."""
    from voice_typer.server.history_db import HistoryDB

    monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
    db = HistoryDB(db_path=tmp_path / "history.db")
    yield db
    db.close()


@pytest.fixture
def templates_dir(tmp_path, monkeypatch):
    """Temporary templates directory with _config_dir monkeypatched."""
    monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def isolated_integrity_cache(tmp_path, monkeypatch):
    """Point the on-disk model-integrity cache at a temp dir."""
    from voice_typer.server import security as security_module

    monkeypatch.setattr(
        security_module,
        "_integrity_cache_path_override",
        tmp_path / "cache" / "integrity_cache.json",
    )
    return tmp_path / "cache" / "integrity_cache.json"


@pytest.fixture(autouse=True)
def clear_binary_path_cache():
    """``voice_typer.server.native_hotkeys.binary_path.get_native_binary_path``"""
    clear_caches()


# The original per-test autouse cleanup (iterating a WeakSet of all live
