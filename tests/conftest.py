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
from unittest.mock import MagicMock

import pytest

# (test infra & config sub-agent): the ``ctypes.WINFUNCTYPE``
# alias previously installed at module-load time has been moved into
# the ``winfunctype_alias`` autouse fixture below. The fixture installs
# the alias per-test via ``monkeypatch.setattr`` so the global
# ``ctypes`` module is no longer mutated at collection time. See the
# fixture docstring for the full rationale.


# dedicated warning category for mock_heavy_imports patch
# failures. Previously these used the bare ``UserWarning`` default,
# which made them impossible to filter separately from real warnings
# emitted by the SUT. Contributors can now filter them with::
#
#     filterwarnings("ignore::tests.conftest.MockHeavyImportsWarning")
#
# or via the ``-W`` CLI flag.
class MockHeavyImportsWarning(UserWarning):
    """Emitted by ``mock_heavy_imports`` when a patch fails.

    Categories of patches that can emit this warning:

    - ``atexit_register``: the ``voice_typer.server.app.atexit.register``
      patch failed (module renamed or attribute moved).
    - ``force_pynput_hotkey_backend``: the hoisted hotkey-backend patch
      failed.
    - ``keyboard_ownership_reset``: the per-test ``keyboard_ownership``
      singleton reset failed.

    XS-46 also dedupes each warning kind to fire at most once per
    pytest session via :data:`_mock_heavy_imports_warned` so a 102-test
    file no longer emits 102 copies of the same warning.
    """


# per-session dedup flag. Each key is a warning kind
# (``"atexit_register"``, ``"force_pynput_hotkey_backend"``,
# ``"keyboard_ownership_reset"``). The first test to hit a given kind
# emits the warning; subsequent tests skip the ``warnings.warn`` call.
# This keeps the pytest output readable when a real module-rename bug
# is present (one warning surfaces the drift; the other 101 tests in
# the file stay quiet).
_mock_heavy_imports_warned: dict[str, bool] = {}


def _warn_once(kind: str, message: str) -> None:
    """Emit a :class:`MockHeavyImportsWarning` at most once per session.

    XS-46: see :class:`MockHeavyImportsWarning` for the rationale.
    """
    if _mock_heavy_imports_warned.get(kind):
        return
    _mock_heavy_imports_warned[kind] = True
    warnings.warn(message, MockHeavyImportsWarning, stacklevel=2)


def pytest_configure(config):
    """TEST-003: register the real_pynput and real_pil markers.

    TASK-013: also register the ``slow`` marker used by
    ``tests/test_manual_slow.py`` to wrap the manual diagnostic
    scripts in ``tests/manual/`` as proper pytest tests. Slow tests
    are deselected by default (see ``pytest_collection_modifyitems``)
    and only run when ``--slow`` is passed.

    XS-45: also register the ``real_torch`` marker for tests that
    genuinely need real ``torch.backends.mps`` semantics (mirrors the
    existing ``real_pynput`` / ``real_pil`` pattern).
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
        "real_torch: opt out of the torch mock (use real torch.backends.mps)",
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


# WAV fixture file ``tests/fixtures/test_440hz_1s_16k.wav``
# (1-second 440Hz sine wave at 16kHz mono, 16-bit PCM) is retained on
# disk for ad-hoc use, but the ``wav_fixture_path`` pytest fixture that
# identified as dead code has been removed — no test imported it.
# Tests that need the WAV file should construct the path inline:
#   ``Path(__file__).parent / "fixtures" / "test_440hz_1s_16k.wav"``


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

    # only mock pynput if the test doesn't request real pynput
    if not request.node.get_closest_marker("real_pynput"):
        mock_pynput = MagicMock()
        mock_pynput_kb = MagicMock()
        monkeypatch.setitem(sys.modules, "pynput", mock_pynput)
        monkeypatch.setitem(sys.modules, "pynput.keyboard", mock_pynput_kb)

    mock_pystray = MagicMock()
    monkeypatch.setitem(sys.modules, "pystray", mock_pystray)

    # only mock PIL if the test doesn't request real PIL
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

    # torch is a heavy optional dep (~17s import cost on the
    # sandbox) that is lazily imported by 6 production modules
    # (transcription.py, dictation_pipeline.py, vad.py,
    # crash_recovery.py, parakeet_engine.py, noise_suppressor.py).
    # Each test that touched those paths previously re-implemented the
    # same local torch mock with drift (some mocked
    # ``torch.backends.mps``, some didn't). Hoisting the mock into the
    # autouse fixture eliminates the 17s import tax and the drift.
    #
    # tests marked with @pytest.mark.real_torch will
    # NOT have torch mocked, so they can exercise real
    # ``torch.backends.mps`` semantics on Apple Silicon.
    if not request.node.get_closest_marker("real_torch"):
        mock_torch = MagicMock(name="mock_torch")

        # Production code at ``voice_typer/server/transcription.py:1260``
        # does ``isinstance(exc, torch.cuda.OutOfMemoryError)``. A bare
        # MagicMock attribute is NOT a type, so ``isinstance`` raises
        # ``TypeError`` (NOT caught by the surrounding
        # ``except (ImportError, AttributeError)`` — the production
        # guard only handles the no-torch-installed case).
        # Fix: expose a real exception subclass at that attribute path
        # so the isinstance check returns False cleanly (the mock is
        # never a real torch, so OOM is never "matched") and the
        # production code falls through to its substring-based MRO
        # check.
        class _FakeOutOfMemoryError(Exception):
            """Mock stand-in for ``torch.cuda.OutOfMemoryError``.

            Real torch's OOM error subclasses ``RuntimeError``; ours
            subclasses ``Exception`` so it doesn't accidentally match
            a real ``RuntimeError`` raised by the SUT (which would
            incorrectly trigger the GPU-fallback path).
            """

        mock_torch.cuda.OutOfMemoryError = _FakeOutOfMemoryError

        # scipy's ``array_api_compat`` dispatcher calls
        # ``is_torch_array(x)`` whenever ``'torch' in sys.modules`` (which
        # is always true under this fixture). That helper does
        # ``isinstance(x, torch.Tensor)`` — and a bare ``MagicMock``
        # attribute is NOT a type, so the call raises
        # ``TypeError: isinstance() arg 2 must be a type`` and crashes
        # any scipy function (``scipy.signal.butter`` / ``lfilter`` /
        # ``resample_poly`` — all used lazily by ``audio_filters/`` and
        # ``recording/resampling.py``) the first time it dispatches on a
        # non-numpy input. The same class of bug also breaks
        # ``issubclass(np.ndarray, torch.Tensor)`` probes used by other
        # array-API-aware libraries. Fix: expose a real (empty) class at
        # ``torch.Tensor`` so the isinstance/issubclass checks return
        # ``False`` cleanly instead of raising.
        class _FakeTensor:
            """Real class so ``isinstance(x, torch.Tensor)`` is valid.

            ``MagicMock`` attributes are not types, so any
            ``isinstance``/``issubclass`` check against ``mock_torch.Tensor``
            raises ``TypeError``. Using a real (empty) class makes those
            checks return ``False`` cleanly — which is the correct
            semantics for a mock torch (nothing is ever a real torch
            tensor under this fixture).
            """

        mock_torch.Tensor = _FakeTensor
        monkeypatch.setitem(sys.modules, "torch", mock_torch)
        # ``torch.backends``, ``torch.backends.mps`` etc. are
        # auto-created child mocks — no explicit per-submodule setitem
        # is needed. ``transformers`` is also mocked because the
        # parakeet_engine + noise_suppressor paths lazily import it.
        monkeypatch.setitem(sys.modules, "transformers", MagicMock(name="mock_transformers"))

    # Prevent atexit handler from polluting test output. :
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
        _warn_once(
            "atexit_register",
            "mock_heavy_imports: could not patch "
            "'voice_typer.server.app.atexit.register' "
            f"({type(exc).__name__}: {exc}); atexit handlers may fire "
            "during tests.",
        )

    # (IMPROVE-mode run, 2026-07-21): hoist the
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
    # replaced ``contextlib.suppress(Exception)`` with targeted
    # ``except (ImportError, AttributeError)`` + ``warnings.warn`` so a
    # renamed module or moved function surfaces as a warning rather
    # than a silent patch-skip.
    try:
        from voice_typer.server.hotkeys import PynputHotkey

        def _force_pynput(hotkey_str):
            return PynputHotkey(hotkey_str)

        # ``create_hotkey_backend`` moved from ``app.py`` to
        # ``voice_typer.server.hotkeys.factory`` (re-exported via
        # ``hotkeys.__init__``).  ``app.py`` no longer has this attribute.
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
    # singleton before each test so stale state from a prior test (e.g.
    # ``set_owner("hotkey_capture")``) doesn't cause ``undo_last`` /
    # ``_cancel_dictation`` to early-return. The singleton persists across
    # tests because it's a class-level ``_instance``; without this reset,
    # test ordering affects test outcomes.
    #
    # replaced ``contextlib.suppress(Exception)`` with targeted
    # ``except (ImportError, AttributeError)`` + ``warnings.warn`` so a
    # renamed singleton or removed ``reset`` method surfaces as a
    # warning rather than a silent skip.
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
    """Reset ``log_rate_limit`` module-level state between tests.

    ``log_rate_limit`` keeps three module-level dicts
    (``_RATE_LIMIT_COUNTS``, ``_RATE_LIMIT_NEXT_SUMMARY_DEADLINE``,
    ``_RATE_LIMIT_SUPPRESSED_SINCE_SUMMARY``) that persist across tests.
    Without this reset, a test that exercises a rate-limited log path
    (e.g. ``test_ipc_no_client_log_redaction``) leaves counters behind
    that cause the *next* test's first call to be suppressed at DEBUG
    instead of emitted at INFO — so ``caplog.at_level(INFO)`` captures
    nothing and the test fails when run after its siblings.

    The reset is autouse so the same leak can't bite future tests that
    exercise ``log_rate_limited``.  Mirrors the ``keyboard_ownership``
    reset inside ``mock_heavy_imports`` (CR-017) and the
    ``clear_binary_path_cache`` autouse pattern (XV-112).
    """
    from voice_typer.server import log_rate_limit

    log_rate_limit.reset()
    yield
    log_rate_limit.reset()


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


# clear ``get_native_binary_path`` LRU cache between tests ──────


@pytest.fixture(autouse=True)
def clear_binary_path_cache():
    """Clear the ``functools.lru_cache`` on
    ``voice_typer.server.native_hotkeys.binary_path.get_native_binary_path``
    before every test.

    XV-112 memoises ``get_native_binary_path()`` with
    ``functools.lru_cache(maxsize=1)`` so production startup doesn't
    re-probe the 6-step lookup chain 3× (factory probe + backend init +
    availability check = ~18 ``Path.is_file()`` stats). Without this
    fixture the cache would persist across tests, breaking the many
    tests that monkeypatch ``sys.platform`` / ``platform.machine`` /
    ``VOICE_TYPER_NATIVE_*`` env vars / ``Path.is_file`` to simulate
    different platform + filesystem states and expect DIFFERENT results
    from successive calls to ``get_native_binary_path()`` within the
    same test session.

    Affected test files (each relies on per-call resolution):
      - ``tests/test_native_hotkeys_binary_path.py``
      - ``tests/test_native_hotkeys.py``
      - ``tests/tauri/test_native_binary_path_tauri.py``
      - ``tests/tauri/mig15/test_native_key_listener_windows.py``
      - ``tests/tauri/mig16/test_native_key_listener_macos.py``
      - ``tests/tauri/mig17/test_native_key_listener_linux.py``

    Tests that monkeypatch ``factory.get_native_binary_path`` directly
    (``tests/test_native_binary_checksum.py``) bypass the real function
    entirely, so they do not need this fixture — but the ``cache_clear``
    call is cheap (one dict pop) and runs unconditionally to keep the
    fixture simple and avoid per-test opt-in drift.

    See ``tests/test_binary_path_caching.py`` for the pinning tests that
    assert the cache actually memoises (and that ``cache_clear`` resets
    it) — those tests use ``monkeypatch.setattr`` to swap the function
    out, so they are unaffected by this fixture.
    """
    # The original early ``return`` on ``ImportError`` for
    # ``native_hotkeys.binary_path`` skipped all subsequent cache
    # clears in this fixture. That was a latent bug: if a future
    # refactor split out ``native_hotkeys`` while keeping ``prewarm``
    # importable, the new ``_resolve_hf_cache_dir`` clear below would
    # silently stop running. Converted to the same try/except/else
    # pattern  already uses so every clear runs independently of
    # the others.
    try:
        from voice_typer.server.native_hotkeys.binary_path import (
            get_native_binary_path,
        )
    except ImportError:
        # Module not importable in this test environment (e.g. a
        # stripped-down test subset). Nothing to clear for THIS path —
        # subsequent clears below still run.
        pass
    else:
        # ``functools.lru_cache`` decorates the function with
        # ``cache_clear``. If a future change removes the decorator,
        # the ``getattr`` guard keeps this fixture a no-op rather than
        # erroring — the affected tests would then start failing
        # (which is the desired signal: the caching contract was
        # broken).
        cache_clear = getattr(get_native_binary_path, "cache_clear", None)
        if cache_clear is not None:
            cache_clear()

    # clear the memoised ``shutil.which`` cache in
    # ``voice_typer.server.clipboard.linux``.  Same rationale as above:
    # tests that monkeypatch ``shutil.which`` to simulate different
    # ``$PATH`` states need a fresh cache per test, otherwise the
    # first test's results leak into subsequent tests.  Tests that
    # monkeypatch ``_cb._have_wl_clipboard`` / ``_cb._have_wtype``
    # directly bypass this cache entirely (the patched attribute
    # replaces the function object on the package namespace), so the
    # ``cache_clear`` is a no-op for them.
    try:
        from voice_typer.server.clipboard.linux import _shutil_which_cached
    except ImportError:
        pass
    else:
        which_cache_clear = getattr(_shutil_which_cached, "cache_clear", None)
        if which_cache_clear is not None:
            which_cache_clear()

    # The ``spawn_background_prewarm`` path now calls
    # ``_pkg.is_prewarm_running()`` at the top, which routes through
    # ``_pid_file_path()`` → ``_config_root()`` →
    # ``_resolve_hf_cache_dir()`` (decorated ``@lru_cache(maxsize=1)``
    # in ``voice_typer/server/prewarm/cache_probe.py``). Without this
    # clear, the ``TestSpawnBackgroundPrewarm`` tests in BOTH
    # ``tests/test_prewarm.py`` and ``tests/test_prewarm_process_tracker.py``
    # pollute the cache with the real ``~/.local/share/voice-typer/huggingface``
    # path, so subsequent ``TestResolveHfCacheDir`` tests (which
    # monkeypatch ``_config_dir`` to a tmp_path) fail because the
    # cached value doesn't match the patched path. The
    # ``_resolve_hf_cache_dir`` docstring claims "Tests clear the cache
    # via ``cache_clear()`` in the autouse fixture" — this block makes
    # that claim true.
    try:
        from voice_typer.server.prewarm.cache_probe import (
            _resolve_hf_cache_dir,
        )
    except ImportError:
        # Module not importable in this test environment — nothing to
        # clear.
        pass
    else:
        resolve_hf_cache_clear = getattr(_resolve_hf_cache_dir, "cache_clear", None)
        if resolve_hf_cache_clear is not None:
            resolve_hf_cache_clear()

    # also clear ``_cached_active_config`` so tests that
    # monkeypatch ``Config.load`` (e.g. test_e2e_smoke's prewarm
    # filter test) don't see a stale cached config from a prior test.
    # Without this clear, the first test that calls
    # ``_active_model_cache_dirs()`` populates the cache, and the
    # second test's ``monkeypatch.setattr(Config, "load", ...)``
    # has no effect because the cached config is returned directly.
    try:
        from voice_typer.server.prewarm.cache_probe import (
            _cached_active_config,
        )
    except ImportError:
        pass
    else:
        cached_cfg_clear = getattr(_cached_active_config, "cache_clear", None)
        if cached_cfg_clear is not None:
            cached_cfg_clear()


# daemon-thread leak prevention ──────────────────────────────────
#
# The original per-test autouse cleanup (iterating a WeakSet of all live
# HistoryDB/CrashRecovery instances and calling close()/shutdown() on each)
# was unsound: it closed instances still held by module-scoped fixtures,
# breaking subsequent tests in the same module and causing an EARLIER
# native crash (53% vs the original 59%). gc.get_referrers() could not
# reliably distinguish "leaked" from "held by a live fixture" because
# fixture-local variables survive on the frame/closure even after teardown.
#
# The correct fix is at the source: each test fixture that constructs an
# ``IPCServer(app)`` via a ``_MockApp`` helper MUST close ``app.history_db``
# and shut down ``app._crash_recovery`` in its teardown. The leaking
# fixtures are fixed individually below and in their respective test files.
# The WeakSet registries (``_LIVE_INSTANCES``) remain in the production
# modules as an observability aid, but no autouse fixture touches them.
