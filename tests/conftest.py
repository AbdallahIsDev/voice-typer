"""Shared autouse fixture: mock heavy imports so all tests run headless.

Modules mocked: sounddevice, faster_whisper, pynput, pystray, PIL,
pyperclip.

TEST-003: the autouse mock is now conditional — tests that need real
pynput (e.g. to test the actual keyboard listener) can use the
``@pytest.mark.real_pynput`` marker to opt out of the pynput mock.

FIX-18 (test infra & config sub-agent): the ``ctypes.WINFUNCTYPE`` alias
that previously lived at module-load time has been moved into the
``winfunctype_alias`` autouse fixture below so the global ``ctypes``
module is no longer mutated at collection time. Tests that exercise
Windows hotkey code paths on Linux (``tests/test_hotkeys_win32.py`` etc.)
still see the alias because the fixture is autouse — it just installs
the alias per-test via ``monkeypatch.setattr`` (auto-undone after each
test) instead of mutating ``ctypes`` permanently for the whole session.

The ``contextlib.suppress(Exception)`` blocks that hid patch failures
have been replaced with targeted ``try/except`` + ``warnings.warn`` so
real failures surface as test warnings instead of being silently
swallowed. Previously a typo in the monkeypatch target (or a renamed
module) would silently skip the patch and tests would pass against an
unpatched code path; now the warning surfaces the drift.

TEST-033: Mocking Convention
============================

This project uses two mocking styles. Follow these rules to keep tests
consistent and maintainable:

1. **Short-lived patches**: Use ``unittest.mock.patch`` as a context
   manager (``with patch(...)``) for patches that only need to exist
   within a single test function. This makes the mock's scope explicit
   and prevents it from leaking to other tests.

2. **Long-lived mocks**: Use ``@pytest.fixture`` for mocks that are
   shared across multiple tests or that need complex setup. Fixtures
   are automatically cleaned up by pytest after each test.

3. **DO NOT mix styles within a single test**: Pick one approach per
   test. If you need both a fixture and a context-manager patch in the
   same test, refactor the patch into the fixture.

4. **``monkeypatch`` vs ``patch``**: Prefer ``monkeypatch`` (pytest's
   built-in) for attribute/item replacement — it's automatically
   undone after the test. Use ``unittest.mock.patch`` only when you
   need the mock object itself (e.g. to assert call counts).

5. **Autouse fixtures**: Use sparingly. The ``mock_heavy_imports``
   fixture below is autouse because every test needs it. New autouse
   fixtures should be justified with a comment explaining why.
"""

import ctypes
import sys
import warnings
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# FIX-18 (test infra & config sub-agent): the ``ctypes.WINFUNCTYPE``
# alias previously installed at module-load time has been moved into
# the ``winfunctype_alias`` autouse fixture below. The fixture installs
# the alias per-test via ``monkeypatch.setattr`` so the global
# ``ctypes`` module is no longer mutated at collection time. See the
# fixture docstring for the full rationale.


def pytest_configure(config):
    """TEST-003: register the real_pynput and real_pil markers.

    TASK-013: also register the ``slow`` marker used by
    ``tests/test_manual_slow.py`` to wrap the manual diagnostic
    scripts in ``tests/manual/`` as proper pytest tests. Slow tests
    are deselected by default (see ``pytest_collection_modifyitems``)
    and only run when ``--slow`` is passed.
    """
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


def pytest_addoption(parser):
    """TASK-013: add ``--slow`` flag to opt in to slow tests.

    Slow tests (marked with ``@pytest.mark.slow``) are skipped by
    default to keep the regular pytest suite fast. Pass ``--slow`` to
    run them — typically in a separate, best-effort CI job.
    """
    parser.addoption(
        "--slow",
        action="store_true",
        default=False,
        help="run slow tests (default: skipped)",
    )


def pytest_collection_modifyitems(config, items):
    """TASK-013: skip slow tests unless ``--slow`` was passed.

    We use ``skip`` (not ``deselect``) so the tests still appear in
    the report as skipped, making it obvious that they exist and
    would have run with ``--slow``.
    """
    if config.getoption("--slow"):
        return
    skip_slow = pytest.mark.skip(reason="need --slow option to run")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip_slow)


# TEST-024: WAV fixture path for audio tests
@pytest.fixture
def wav_fixture_path():
    """Return the path to the test WAV fixture file.

    The fixture is a 1-second 440Hz sine wave at 16kHz mono, 16-bit PCM.
    See tests/fixtures/README.md for details.
    """
    path = Path(__file__).resolve().parent / "fixtures" / "test_440hz_1s_16k.wav"
    assert path.exists(), f"WAV fixture not found at {path}"
    return path


@pytest.fixture(autouse=True)
def winfunctype_alias(monkeypatch):
    """Provide ``ctypes.WINFUNCTYPE`` on non-Windows platforms.

    ``voice_typer.server.hotkeys._install_low_level_hook`` (line ~1311)
    and ``voice_typer.server.microphone_watcher`` use
    ``ctypes.WINFUNCTYPE(...)`` inside Windows-gated function bodies.
    On Linux, those code paths are never executed in production, but
    some tests (``tests/test_hotkeys_win32.py``) DO exercise them with
    mocked Windows state. Without the alias, those tests fail with
    ``AttributeError: module 'ctypes' has no attribute 'WINFUNCTYPE'``.

    Aliasing ``WINFUNCTYPE = CFUNCTYPE`` on non-Windows lets those
    tests run. This is a test-only shim — production behaviour on
    Windows is unchanged (real ``WINFUNCTYPE`` is used; on Windows the
    alias is a no-op because ``hasattr(ctypes, "WINFUNCTYPE")`` is
    True).

    FIX-18: previously this alias was installed at conftest.py
    module-load time via ``ctypes.WINFUNCTYPE = ctypes.CFUNCTYPE``.
    That permanently mutated the global ``ctypes`` module for the
    whole pytest session, which (a) leaked the alias into any
    non-test code that ran in the same process and (b) made it
    impossible for a test to assert that ``WINFUNCTYPE`` is ABSENT
    on non-Windows (e.g. to verify the production guard). Moving the
    alias into an autouse fixture means it's installed only for the
    duration of each test and is automatically removed by
    ``monkeypatch`` afterwards, restoring ``ctypes`` to its pristine
    state between tests.
    """
    if not hasattr(ctypes, "WINFUNCTYPE"):
        monkeypatch.setattr(
            ctypes,
            "WINFUNCTYPE",
            ctypes.CFUNCTYPE,
            raising=False,
        )


@pytest.fixture(autouse=True)
def mock_heavy_imports(monkeypatch, request):
    """Mock all hardware/GUI dependencies so tests run headless.

    TEST-003: tests marked with @pytest.mark.real_pynput will NOT
    have pynput mocked, so they can test the real keyboard listener.
    """
    mock_sd = MagicMock()
    mock_sd.query_devices.return_value = []
    monkeypatch.setitem(sys.modules, "sounddevice", mock_sd)

    mock_whisper = MagicMock()
    monkeypatch.setitem(sys.modules, "faster_whisper", mock_whisper)
    monkeypatch.setitem(sys.modules, "faster_whisper.WhisperModel", MagicMock())

    # TEST-003: only mock pynput if the test doesn't request real pynput
    if not request.node.get_closest_marker("real_pynput"):
        mock_pynput = MagicMock()
        mock_pynput_kb = MagicMock()
        monkeypatch.setitem(sys.modules, "pynput", mock_pynput)
        monkeypatch.setitem(sys.modules, "pynput.keyboard", mock_pynput_kb)

    mock_pystray = MagicMock()
    monkeypatch.setitem(sys.modules, "pystray", mock_pystray)

    # TEST-033: only mock PIL if the test doesn't request real PIL
    if not request.node.get_closest_marker("real_pil"):
        mock_pil = MagicMock()
        monkeypatch.setitem(sys.modules, "PIL", mock_pil)
        monkeypatch.setitem(sys.modules, "PIL.Image", MagicMock())
        monkeypatch.setitem(sys.modules, "PIL.ImageDraw", MagicMock())
    else:
        # Ensure the real PIL is available in sys.modules.
        #
        # Some test modules (e.g. tests/test_tray.py) call
        # ``sys.modules.setdefault("PIL", MagicMock())`` at *collection*
        # time, which permanently installs a MagicMock for PIL in
        # sys.modules. When a ``real_pil`` test runs afterwards, a plain
        # ``import PIL`` returns that MagicMock instead of the real
        # package, causing ``PIL.ImageDraw`` attribute access to fail
        # with ``AttributeError: module 'PIL' has no attribute
        # 'ImageDraw'``.
        #
        # Fix: detect and evict any mock entries for PIL/PIL.Image/
        # PIL.ImageDraw from sys.modules before importing the real
        # package. We identify mocks by checking ``__spec__`` — real
        # modules have a non-None ``__spec__``; MagicMocks do not.
        for _key in ("PIL", "PIL.Image", "PIL.ImageDraw"):
            _existing = sys.modules.get(_key)
            if _existing is not None and getattr(_existing, "__spec__", None) is None:
                # Looks like a mock (or a non-module object) — evict it
                # so the real import below actually loads the package.
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
            pass  # PIL not available — tests will skip

    monkeypatch.setitem(sys.modules, "pyperclip", MagicMock())

    # Prevent atexit handler from polluting test output. FIX-18:
    # previously this was wrapped in ``contextlib.suppress(Exception)``,
    # which silently swallowed typos in the monkeypatch target and let
    # tests pass against unpatched code. The targeted ``except`` below
    # only catches the two real failure modes (the module isn't
    # importable, or the attribute is missing) and warns on either so
    # drift is visible in CI output without failing the test.
    try:
        monkeypatch.setattr(
            "voice_typer.server.app.atexit.register",
            lambda *a, **kw: None,
        )
    except (ImportError, AttributeError) as exc:
        warnings.warn(
            "mock_heavy_imports: could not patch "
            "'voice_typer.server.app.atexit.register' "
            f"({type(exc).__name__}: {exc}); atexit handlers may fire "
            "during tests.",
            stacklevel=2,
        )

    # CR-068 (IMPROVE-mode run, 2026-07-21): hoist the
    # ``force_pynput_hotkey_backend`` patch from the deleted
    # ``tests/test_app.py:76-88`` into this autouse fixture so
    # ``tests/app/test_hotkeys.py`` (and other hotkey tests) work on
    # macOS/Windows where the default hotkey backend is NOT PynputHotkey.
    # Pre-fix, the tests passed only because on Linux/X11 the unpatched
    # ``create_hotkey_backend`` falls through to PynputHotkey by default
    # — same accidental pass condition documented in the (now-deleted)
    # ``tests/test_app.py:73-75``. With the hoist, the patch is applied
    # uniformly across platforms.
    #
    # FIX-18: replaced ``contextlib.suppress(Exception)`` with targeted
    # ``except (ImportError, AttributeError)`` + ``warnings.warn`` so a
    # renamed module or moved function surfaces as a warning rather
    # than a silent patch-skip.
    try:
        from voice_typer.server.hotkeys import PynputHotkey

        def _force_pynput(hotkey_str):
            return PynputHotkey(hotkey_str)

        monkeypatch.setattr("voice_typer.server.app.create_hotkey_backend", _force_pynput)
        monkeypatch.setattr(
            "voice_typer.server.hotkey_dispatcher.create_hotkey_backend",
            _force_pynput,
        )
    except (ImportError, AttributeError) as exc:
        warnings.warn(
            "mock_heavy_imports: could not hoist "
            "force_pynput_hotkey_backend patch "
            f"({type(exc).__name__}: {exc}); hotkey tests may fail "
            "on non-Linux platforms.",
            stacklevel=2,
        )

    # CR-017 (IMPROVE-mode run, 2026-07-21): reset the keyboard_ownership
    # singleton before each test so stale state from a prior test (e.g.
    # ``set_owner("hotkey_capture")``) doesn't cause ``undo_last`` /
    # ``_cancel_dictation`` to early-return. The singleton persists across
    # tests because it's a class-level ``_instance``; without this reset,
    # test ordering affects test outcomes.
    #
    # FIX-18: replaced ``contextlib.suppress(Exception)`` with targeted
    # ``except (ImportError, AttributeError)`` + ``warnings.warn`` so a
    # renamed singleton or removed ``reset`` method surfaces as a
    # warning rather than a silent skip.
    try:
        from voice_typer.server.keyboard_ownership import keyboard_ownership

        keyboard_ownership().reset()
    except (ImportError, AttributeError) as exc:
        warnings.warn(
            "mock_heavy_imports: could not reset keyboard_ownership "
            "singleton "
            f"({type(exc).__name__}: {exc}); hotkey ownership state "
            "may leak between tests.",
            stacklevel=2,
        )


# ── Shared fixtures for domain-split test files ────────────────────────


@pytest.fixture
def tmp_config_dir(tmp_path, monkeypatch):
    """Temporary config directory with _config_dir monkeypatched."""
    monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
    return tmp_path


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
