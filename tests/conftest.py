"""Shared autouse fixture: mock heavy imports so all tests run headless.

Modules mocked: sounddevice, faster_whisper, pynput, pystray, PIL,
pyperclip.

the autouse mock is now conditional — tests that need real
pynput (e.g. to test the actual keyboard listener) can use the
``@pytest.mark.real_pynput`` marker to opt out of the pynput mock.

(test infra & config sub-agent): the ``ctypes.WINFUNCTYPE`` alias
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

Mocking Convention
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
import time
import warnings
from collections.abc import Callable
from unittest.mock import MagicMock

import pytest

from tests.fixtures.cache_resets import clear_caches


def wait_until(
    predicate: Callable[[], bool],
    *,
    timeout: float = 2.0,
    interval: float = 0.01,
    msg: str | None = None,
) -> None:
    """Poll ``predicate`` until it returns truthy or ``timeout`` elapses.

    Drop-in replacement for ``time.sleep(N)`` + ``assert condition``
    patterns that are flakiness-prone. Raises ``AssertionError`` with
    ``msg`` (or a synthesized message) on timeout so the failure
    surfaces in the test report instead of silently passing because the
    sleep was slightly too short.

    For thread-synchronization tests, prefer ``threading.Event.wait(timeout)``
    over this helper — ``Event.wait`` is non-busy and deterministic.
    ``wait_until`` is appropriate when no ``Event``/``Condition`` is
    available (e.g. waiting for a side effect on a MagicMock, a file on
    disk, or a thread state the test can't directly observe).

    Migrating ``time.sleep`` call sites: replace ::

        time.sleep(0.5)
        assert obj.ready

    with ::

        wait_until(lambda: obj.ready, timeout=2.0,
                   msg="obj.ready did not become True within 2s")

    The default ``interval=0.01`` keeps the poll loop responsive without
    burning CPU; for sub-millisecond synchronization, use ``Event.wait``
    instead. The default ``timeout=2.0`` is generous enough for most
    thread-scheduling latency on a loaded CI runner.

    Introduced as part of the effort to migrate the
    codebase's 99+ ``time.sleep`` calls in test files to deterministic
    waiters. This helper is the canonical replacement; the migration of
    individual call sites is incremental.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(interval)
    detail = msg or f"wait_until did not satisfy within {timeout}s"
    raise AssertionError(detail)


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

    also dedupes each warning kind to fire at most once per
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

    see :class:`MockHeavyImportsWarning` for the rationale.
    """
    if _mock_heavy_imports_warned.get(kind):
        return
    _mock_heavy_imports_warned[kind] = True
    warnings.warn(message, MockHeavyImportsWarning, stacklevel=2)


def pytest_configure(config):
    """register the real_pynput and real_pil markers.

    also register the ``slow`` marker used by
    ``tests/test_manual_slow.py`` to wrap the manual diagnostic
    scripts in ``tests/manual/`` as proper pytest tests. Slow tests
    are deselected by default (see ``pytest_collection_modifyitems``)
    and only run when ``--slow`` is passed.

    also register the ``real_torch`` marker for tests that
    genuinely need real ``torch.backends.mps`` semantics (mirrors the
    existing ``real_pynput`` / ``real_pil`` pattern).

    also register a project-wide hypothesis profile named ``ci`` with
    ``deadline=None`` and load it unconditionally. Hypothesis's default
    ``deadline`` is 200ms per test case; on a loaded CI runner (or even
    a busy local machine) the 22 ``@settings``-decorated tests across
    ``tests/test_property_based.py``,
    ``tests/test_text_cleanup_hypothesis.py`` and
    ``tests/test_streaming_hypothesis.py`` can exceed 200ms and fail
    with ``FlakyFailure(DeadlineExceeded)``. Registering ``deadline=None``
    as the parent profile means every ``@settings(max_examples=N)``
    decorator inherits ``deadline=None`` from the loaded profile, so
    the deadline is disabled project-wide without touching each of the
    22 call sites. See:
    https://hypothesis.readthedocs.io/en/latest/settings.html#hypothesis.settings.register_profile

    The profile is loaded unconditionally (not just under CI) because
    a developer running ``pytest tests/test_property_based.py`` locally
    on a busy laptop hits the same ``DeadlineExceeded`` failures as CI.
    ``deadline=None`` is the hypothesis-recommended default for test
    suites that aren't doing real-time performance work; the only
    downside of disabling it is that a pathological slowdown (e.g. an
    accidental O(n²) inner loop) is no longer surfaced by hypothesis —
    but pytest-timeout (``--timeout=60``) still catches hangs.

    Hypothesis is an optional dependency (declared in
    ``[project.optional-dependencies].test``); the import is wrapped in
    ``try/except ImportError`` so a minimal install without ``[test]``
    extras still collects successfully (the hypothesis test files have
    their own ``pytestmark = pytest.mark.skipif(not HAS_HYPOTHESIS, ...)``
    guard and skip cleanly).
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

    # Register + load a project-wide hypothesis profile with
    # ``deadline=None``. See the docstring above for the full rationale.
    # The import is lazy (inside pytest_configure, not at module load)
    # so a minimal install without hypothesis can still collect tests.
    try:
        from hypothesis import HealthCheck, settings
    except ImportError:
        # hypothesis is optional — skip profile registration. The
        # hypothesis test files skip themselves via their own
        # ``pytestmark = pytest.mark.skipif(not HAS_HYPOTHESIS, ...)``.
        return

    # ``suppress_health_check=[HealthCheck.too_slow]`` mirrors the
    # per-test ``@settings(suppress_health_check=[HealthCheck.too_slow])``
    # decorators already present in the hypothesis test files — setting
    # it at the profile level makes the suppression project-wide so a
    # future ``@given`` test that forgets the decorator is still safe.
    # ``print_blob=True`` makes hypothesis include a reproducible
    # base64 blob in failure messages so a CI failure can be reproduced
    # locally with ``--hypothesis-seed=...`` or by pasting the blob.
    settings.register_profile(
        "ci",
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
        print_blob=True,
    )
    # ``load_profile`` is idempotent and safe to call multiple times
    # across nested conftest.py files. Loading here (rather than relying
    # on the ``HYPOTHESIS_PROFILE=ci`` env var) means the profile is
    # active for every pytest invocation, including local ``pytest
    # tests/test_foo.py`` runs that don't go through CI.
    settings.load_profile("ci")


def pytest_addoption(parser):
    """add ``--slow`` flag to opt in to slow tests.

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
    """skip slow tests unless ``--slow`` was passed.

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

    previously this alias was installed at conftest.py
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


# ── Heavy-import mocking: session + per-test split ────────────────────
#
# Originally a single autouse ``mock_heavy_imports`` fixture ran at
# function scope for every one of the ~12k tests, installing the SAME
# unconditional mocks (sounddevice, faster_whisper, pystray, pyperclip,
# torch, transformers) each time. The mocks are identical across every
# test, so the per-test ``sys.modules.setitem`` calls (~6) + MagicMock
# constructions (~4) were pure overhead — ~120k redundant operations
# per session.
#
# The split below moves the unconditional mocks into a session-scoped
# fixture (``mock_heavy_imports_session``) that runs once per worker.
# The per-test ``mock_heavy_imports`` fixture retains ONLY the work
# that actually varies per test:
#
#   - pynput marker branch (``real_pynput`` opts out of the pynput mock)
#   - PIL marker branch (``real_pil`` opts out + evicts stale mocks)
#   - torch marker branch (``real_torch`` opts out + evicts the session
#     torch mock so a real ``torch.backends.mps`` import succeeds)
#   - ``atexit.register`` patch (per-test, imports ``voice_typer.server.app``)
#   - ``force_pynput_hotkey_backend`` patch (per-test, imports
#     ``voice_typer.server.hotkeys``)
#   - ``keyboard_ownership`` singleton reset (per-test, MUST run before
#     each test to prevent state leakage)
#
# The per-test fixture name is intentionally kept as ``mock_heavy_imports``
# (NOT renamed to ``mock_heavy_imports_per_test`` as a strict reading of
# the task spec might suggest) because EIGHT test files override the
# conftest fixture by redefining their own ``mock_heavy_imports`` at
# function scope (run ``rg '^def mock_heavy_imports\b' tests/`` to
# re-verify the list when adding or removing an override):
#
#   - tests/test_recorder_double_resample.py (custom sounddevice mock —
#     stubs ``query_devices`` per-test to simulate a 48 kHz native-rate
#     microphone, the  double-resample failure scenario)
#   - tests/test_recording_audio_processor.py (custom sounddevice mock
#     with a ``FakeInputStream`` that captures the callback for direct
#     chunk-push invocation from the test)
#   - tests/test_tray.py (custom pystray/PIL mocks — installs a
#     ``_FakeIcon`` / ``_FakeMenu`` / ``_FakeMenuItem`` so the tray
#     menu builder runs headless without invoking real GTK/Cocoa/Win32)
#   - tests/test_volume_lifecycle.py (full hardware/GUI mock set +
#     custom ``create_hotkey_backend`` patch that forces PynputHotkey so
#     the test can mock ``pynput.keyboard.GlobalHotKeys``; this is a
#     near-complete replacement of the conftest fixture, not just the
#     hotkey patch)
#   - tests/test_shutdown_parallel.py (no-op override to avoid
#     importing ``voice_typer.server.app`` — uses a ``_FakeApp`` and
#     injects mock modules into ``sys.modules`` directly)
#   - tests/test_shutdown_deadline.py (no-op override — same rationale
#     as test_shutdown_parallel.py; uses a ``_FakeApp``)
#   - tests/test_shutdown_race_fixes.py (no-op override — same rationale;
#     exercises ``_do_fast_cleanup`` against a ``_FakeApp``)
#   - tests/test_shutdown_plan_zr17.py (no-op override — same rationale;
#     exercises the ZR-17 shutdown plan against a ``_FakeApp``)
#
# Renaming the conftest fixture would silently break those overrides:
# the local ``mock_heavy_imports`` would no longer shadow the conftest
# autouse fixture, and the conftest version would run alongside the
# local one — for the four ``test_shutdown_*`` files this would
# re-introduce the ``voice_typer.server.app`` import that the override
# deliberately avoids (the real app module can be in a parallel agent's
# WIP state during incremental development). Keeping the original name
# preserves the shadow semantics.
# The session-scoped fixture uses a distinct name
# (``mock_heavy_imports_session``) so there is no collision.


class _FakeOutOfMemoryError(Exception):
    """Mock stand-in for ``torch.cuda.OutOfMemoryError``.

    Real torch's OOM error subclasses ``RuntimeError``; ours subclasses
    ``Exception`` so it doesn't accidentally match a real
    ``RuntimeError`` raised by the SUT (which would incorrectly trigger
    the GPU-fallback path in
    ``voice_typer/server/transcription.py:1260``).
    """


class _FakeTensor:
    """Real class so ``isinstance(x, torch.Tensor)`` is valid.

    ``MagicMock`` attributes are not types, so any
    ``isinstance``/``issubclass`` check against ``mock_torch.Tensor``
    raises ``TypeError``. Using a real (empty) class makes those checks
    return ``False`` cleanly — which is the correct semantics for a
    mock torch (nothing is ever a real torch tensor under this
    fixture).

    Defined at module level (rather than inside the session fixture)
    so the class object is shared across the whole session — ``isinstance``
    identity checks against ``torch.Tensor`` are stable, not
    re-instantiated per session setup.
    """


def _build_mock_torch() -> MagicMock:
    """Construct the session-shared ``torch`` MagicMock.

    Centralised so the session fixture stays readable and so a future
    test that needs the same mock torch (without going through
    ``sys.modules``) can call this directly. The two real-class
    attributes (``cuda.OutOfMemoryError`` + ``Tensor``) are populated
    here, not via bare MagicMock auto-attribute, because production
    code does ``isinstance(exc, torch.cuda.OutOfMemoryError)`` and
    scipy's ``array_api_compat`` does ``isinstance(x, torch.Tensor)`` —
    both raise ``TypeError`` against a MagicMock attribute.
    """
    mock_torch = MagicMock(name="mock_torch")
    mock_torch.cuda.OutOfMemoryError = _FakeOutOfMemoryError
    mock_torch.Tensor = _FakeTensor
    return mock_torch


@pytest.fixture(scope="session", autouse=True)
def mock_heavy_imports_session():
    """Install unconditional heavy-import mocks once per worker session.

    These six mocks (sounddevice, faster_whisper, faster_whisper.WhisperModel,
    pystray, pyperclip, torch, transformers) are identical across every
    test. Moving them from the per-test ``mock_heavy_imports`` fixture
    to this session-scoped fixture eliminates ~6 ``sys.modules.setitem``
    + ~4 ``MagicMock`` constructions per test × ~12k tests.

    Uses the ``pytest.MonkeyPatch()`` factory (instead of the
    ``monkeypatch`` fixture, which is function-scoped and cannot be
    requested from a session-scoped fixture). Cleanup happens in the
    ``finally`` block via ``mp.undo()`` so the session teardown
    restores ``sys.modules`` to its pre-session state — important when
    running multiple pytest invocations in the same interpreter (e.g.
    via ``pytest.main()`` in a notebook).

    Tests marked ``@pytest.mark.real_torch`` evict the session torch
    mock in the per-test ``mock_heavy_imports`` fixture (mirroring the
    ``real_pil`` eviction pattern) and import the real ``torch``
    package, so they can exercise real ``torch.backends.mps`` semantics
    on Apple Silicon. No test currently uses ``real_torch`` (the marker
    was registered for future use), but the eviction branch is in
    place to keep the contract symmetric with ``real_pil``.

    Per-test local overrides of ``mock_heavy_imports`` (in
    ``tests/test_shutdown_plan_zr17.py``, ``tests/test_volume_lifecycle.py``,
    etc.) do NOT shadow this session fixture — they shadow only the
    function-scoped ``mock_heavy_imports`` of the same name. So every
    test (including those with local overrides) gets the session mocks
    installed; the local override only replaces the per-test portion.
    This is intentional and matches the original behaviour for the
    unconditional mocks.
    """
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

        # ``torch.backends``, ``torch.backends.mps`` etc. are
        # auto-created child mocks — no explicit per-submodule setitem
        # is needed. ``transformers`` is also mocked because the
        # parakeet_engine + noise_suppressor paths lazily import it.
        mp.setitem(sys.modules, "torch", _build_mock_torch())
        mp.setitem(sys.modules, "transformers", MagicMock(name="mock_transformers"))

        yield
    finally:
        mp.undo()


@pytest.fixture(autouse=True)
def mock_heavy_imports(monkeypatch, request):
    """Per-test conditional mocks + atexit + keyboard_ownership reset.

    Handles ONLY the work that varies per test:

      - ``real_pynput`` marker branch (opt out of the pynput mock).
      - ``real_pil`` marker branch (opt out + evict stale PIL mocks
        that test modules may have installed at collection time).
      - ``real_torch`` marker branch (opt out + evict the session
        torch mock so a real ``torch.backends.mps`` import succeeds).
      - ``atexit.register`` patch (prevents production atexit handlers
        from polluting test output).
      - ``force_pynput_hotkey_backend`` patch (uniform PynputHotkey
        backend across platforms — without it, hotkey tests only pass
        on Linux/X11 by accident).
      - ``keyboard_ownership`` singleton reset (prevents stale owner
        state from a prior test leaking into the next).

    The unconditional mocks (sounddevice, faster_whisper, pystray,
    pyperclip, torch, transformers) are installed ONCE per session by
    :func:`mock_heavy_imports_session` and are NOT re-installed here.

    tests marked with @pytest.mark.real_pynput will NOT
    have pynput mocked, so they can test the real keyboard listener.
    """
    # only mock pynput if the test doesn't request real pynput
    if not request.node.get_closest_marker("real_pynput"):
        mock_pynput = MagicMock()
        mock_pynput_kb = MagicMock()
        monkeypatch.setitem(sys.modules, "pynput", mock_pynput)
        monkeypatch.setitem(sys.modules, "pynput.keyboard", mock_pynput_kb)

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
        #
        # Note: the session-scoped ``mock_heavy_imports_session`` does
        # NOT mock PIL (it's intentionally per-test because of this
        # eviction branch), so the only stale mock to evict is one
        # left behind by a prior test's collection-time setdefault.
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

    # torch is installed unconditionally by the session-scoped
    # ``mock_heavy_imports_session`` fixture. The ``real_torch`` marker
    # branch below mirrors the ``real_pil`` eviction pattern: detect
    # the session-installed mock (it has no ``__spec__``), evict it,
    # and import the real ``torch`` package so tests marked
    # ``@pytest.mark.real_torch`` can exercise real
    # ``torch.backends.mps`` semantics on Apple Silicon. No test
    # currently uses the marker, but the branch is in place to keep
    # the contract symmetric with ``real_pil``.
    #
    # ``transformers`` is also evicted because the session fixture
    # mocks it alongside ``torch`` — a ``real_torch`` test almost
    # certainly wants real ``transformers`` too (the two are imported
    # together by ``parakeet_engine`` + ``noise_suppressor``).
    if request.node.get_closest_marker("real_torch"):
        for _key in ("torch", "transformers"):
            _existing = sys.modules.get(_key)
            if _existing is not None and getattr(_existing, "__spec__", None) is None:
                # Looks like the session mock (no ``__spec__``) — evict
                # it so the real import below actually loads the package.
                del sys.modules[_key]
        try:
            import importlib as _importlib

            _real_torch = _importlib.import_module("torch")
            monkeypatch.setitem(sys.modules, "torch", _real_torch)
        except ImportError:
            # torch not available — the test will likely skip via
            # ``pytest.importorskip("torch")``. Leave sys.modules
            # without torch; the session mock was already evicted.
            pass

    # Prevent atexit handler from polluting test output. :
    # previously this was wrapped in ``contextlib.suppress(Exception)``,
    # which silently swallowed typos in the monkeypatch target and let
    # tests pass against unpatched code. The targeted ``except`` below
    # only catches the two real failure modes (the module isn't
    # importable, or the attribute is missing) and warns on either so
    # drift is visible in CI output without failing the test.
    #
    # Per-test (NOT session) because: (a) it imports
    # ``voice_typer.server.app``, which test_shutdown_plan_zr17.py
    # explicitly avoids via its local no-op override of
    # ``mock_heavy_imports``; (b) the patch is idempotent so per-test
    # re-installation is cheap; (c) keeping it per-test preserves the
    # shadow semantics of the four local overrides.
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
    #
    # Per-test (NOT session) because: (a) it imports
    # ``voice_typer.server.hotkeys``, which some local overrides
    # (test_volume_lifecycle.py) re-implement with a different patch
    # target (``app.create_hotkey_backend`` vs
    # ``hotkey_dispatcher.create_hotkey_backend``); keeping it per-test
    # preserves the shadow semantics.
    try:
        from voice_typer.server.hotkeys import PynputHotkey

        # ``create_hotkey_backend`` now accepts a ``role`` kwarg (used to
        # give Wayland backends a per-role socket path). The mock must
        # tolerate it — ``PynputHotkey`` does not take ``role``, so it is
        # accepted and dropped here.
        def _force_pynput(hotkey_str, role=None, **kwargs):
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
    reset inside ``mock_heavy_imports`` (the fix) and the
    ``clear_binary_path_cache`` autouse pattern (the fix).
    """
    from voice_typer.server import log_rate_limit

    log_rate_limit.reset()
    yield
    log_rate_limit.reset()


# ── Shared fixtures for domain-split test files ────────────────────────


@pytest.fixture
def tmp_config_dir(tmp_path, monkeypatch):
    """Temporary config directory with ``_config_dir`` monkeypatched.

    Patches BOTH ``voice_typer.server.config._config_dir`` (the
    canonical accessor) AND ``voice_typer.server.app._config_dir`` (the
    bound reference inside ``app.py``).

    ``app.py`` imports ``_config_dir`` via
    ``from voice_typer.server.config import ... _config_dir``, which
    binds the function object into the ``app`` module's namespace at
    import time. Patching only ``config._config_dir`` leaves
    ``app._config_dir`` pointing at the original function — so code
    paths inside ``app.py`` that call ``_config_dir()`` directly (e.g.
    ``DuckCrashRecovery(config_dir=_config_dir())`` near the standalone
    launch path) silently write to the real
    ``~/.local/share/voice-typer/`` directory instead of ``tmp_path``.

    Previously 4 test files (``test_app_restart.py``,
    ``test_app_cleanup.py``, ``test_shutdown_controller.py``,
    ``test_shutdown_posix_release.py``) shadowed this fixture locally
    with a version that patched both references. Those local shadows
    have been deleted in favour of this single canonical fixture so
    the project-wide fixture is the source of truth.
    """
    monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
    monkeypatch.setattr("voice_typer.server.app._config_dir", lambda: tmp_path)
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


@pytest.fixture
def isolated_integrity_cache(tmp_path, monkeypatch):
    """Point the on-disk model-integrity cache at a temp dir.

    ``security.verify_model_integrity`` persists its memoized SHA-256
    hashes to ``<config_dir>/cache/integrity_cache.json`` via
    ``_integrity_cache_path()`` (which honors the
    ``_integrity_cache_path_override`` module hook). Tests that exercise
    the real verifier would otherwise write real user state under the
    developer's config directory — which can be read-only or
    ACL-restricted (causing ``tempfile.mkstemp`` inside
    ``_secure_atomic_write`` to hang) and pollute real cache data
    across runs. Request this fixture from any test class that calls
    ``verify_model_integrity`` (e.g. model-integrity, parakeet/qwen
    engine hard-fail tests).
    """
    from voice_typer.server import security as security_module

    monkeypatch.setattr(
        security_module,
        "_integrity_cache_path_override",
        tmp_path / "cache" / "integrity_cache.json",
    )
    return tmp_path / "cache" / "integrity_cache.json"


# clear ``get_native_binary_path`` LRU cache between tests ──────


@pytest.fixture(autouse=True)
def clear_binary_path_cache():
    """Clear the ``functools.lru_cache`` on
    ``voice_typer.server.native_hotkeys.binary_path.get_native_binary_path``
    (and three other production caches) before every test.

    memoises ``get_native_binary_path()`` with
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

    The four caches cleared here (and the per-cache rationale for each)
    are tabulated in :data:`tests.fixtures.cache_resets.CACHES_TO_CLEAR`
    and iterated by :func:`tests.fixtures.cache_resets.clear_caches`.
    Moving the loop out of this fixture (and out of ``conftest.py``)
    means adding a fifth cached callable is now a one-line table edit
    instead of another copy-pasted ``try/except ImportError`` block
    here — which was the latent-bug vector that bit us once already
    (the original early ``return`` on ``ImportError`` for
    ``native_hotkeys.binary_path`` silently skipped all subsequent
    clears; converting to per-entry ``try/except`` + a table makes the
    same drift mechanically impossible).

    Each entry's clear is independent — a missing optional dependency
    (e.g. ``clipboard.linux`` on Windows-only test runs) raises
    ``ImportError`` for THAT entry only; the loop moves on. Same
    semantics as the original copy-pasted blocks, just table-driven.
    """
    clear_caches()


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
