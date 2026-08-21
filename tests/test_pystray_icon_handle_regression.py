"""S2-stopgap regression test for pystray private ``_icon_handle``.

Voice Typer pins ``pystray>=0.19,<0.20`` in ``pyproject.toml`` because
``voice_typer/server/tray.py:_apply_state`` reaches into pystray's
private ``_icon_handle`` attribute to work around a Win32 DestroyIcon
bug (``WinError 1402``) on rapid icon updates. The private attribute
has been stable across 0.19.x but is NOT part of the public API —
a future 0.20+ release could rename or remove it, silently breaking
the DestroyIcon workaround so that rapid icon updates on Windows
start hitting ``OSError: WinError 1402`` again with no diagnostic
surface.

TODO S2-file an upstream pystray issue asking for a
public ``reset_icon_handle()`` API. Once upstream exposes it:

  1. Bump ``pystray>=0.20`` (or whichever release exposes the API).
  2. Replace the ``_icon_handle`` access in
     ``voice_typer/server/tray.py:_apply_state`` with the new public
     API.
  3. Update / replace ``test_pystray_icon_class_exposes_icon_handle``
     to assert the new public API instead of the private attribute.

Until then, this module is a STOPGAP: when pystray is bumped to 0.20+
and ``_icon_handle`` is removed, these tests FAIL LOUDLY at CI time
instead of letting the DestroyIcon workaround silently degrade.

Tests in this module
--------------------

* ``test_pystray_icon_class_exposes_icon_handle`` — the primary
  regression test. Loads the *real* ``pystray`` package (bypassing
  the autouse ``MagicMock`` installed by ``tests/conftest.py``) and
  asserts ``hasattr(pystray.Icon, "_icon_handle")``. Windows-only
  (skipif): the private ``_icon_handle`` attribute exists only in
  pystray's Win32 backend. Skips cleanly if real pystray is not
  installed in the test environment (e.g. minimal Linux sandbox
  without GUI deps).

* ``test_apply_state_warns_when_icon_handle_missing`` — verifies
  the graceful fallback in ``tray.py:_apply_state``: when
  ``_icon_handle`` is missing AND ``self._icon.icon = ...`` raises
  ``OSError``, ``_apply_state`` logs a WARNING and returns normally
  (never crashes). This ensures a future pystray release that
  renames ``_icon_handle`` fails visibly in logs instead of
  silently swallowing the DestroyIcon OSError.

* ``test_apply_state_clears_icon_handle_when_present`` —
  belt-and-braces: when ``_icon_handle`` IS present and ``OSError``
  is raised, the workaround fires (``_icon_handle`` is set to
  ``None``) and no warning is logged. This guards the existing
   / GT-E1-8 workaround from regressing.
"""

from __future__ import annotations

import importlib
import sys

import pytest
from voice_typer.server.tray import TrayIcon  # noqa: E402
from voice_typer.server.tray_types import AppState  # noqa: E402


def _load_real_pystray():
    """Load the *real* ``pystray`` package, bypassing the autouse mock.

    The autouse ``mock_heavy_imports`` fixture in
    ``tests/conftest.py`` installs ``sys.modules["pystray"] =
    MagicMock()`` for every test so the rest of the suite can run
    headless. This regression test needs the *real* pystray package
    so it can introspect the actual ``pystray.Icon`` class for the
    private ``_icon_handle`` attribute. We:

      1. Save the current (mock) ``sys.modules["pystray"]`` entry.
      2. Evict ``pystray`` and any ``pystray.*`` submodules from the
         import cache so ``importlib.import_module`` does a real
         load from disk.
      3. ``importlib.import_module("pystray")`` returns the real
         module (or raises ``ImportError`` if pystray isn't
         installed — e.g. minimal Linux sandbox without GUI deps).
      4. The ``finally`` block restores the mock so the rest of
         the test session still sees the autouse-mocked pystray
         (other tests in this process rely on the mock).

    Returns the real pystray module, or ``None`` if it is not
    installed (in which case the caller should ``pytest.skip``).

    NOTE: this catches ``Exception``, NOT just ``ImportError`` — on a
    headless Linux box without a ``$DISPLAY``, importing ``pystray``
    eagerly calls ``Xlib.display.Display()`` which raises
    ``Xlib.error.DisplayNameError`` (NOT an ``ImportError`` subclass).
    The old ``except ImportError`` clause let that exception surface
    and crash the test; broadening to ``Exception`` makes the skip
    path trigger cleanly so the test degrades to a SKIP on headless
    CI instead of an ERROR.
    """
    saved = sys.modules.get("pystray")
    keys_to_evict = [k for k in list(sys.modules.keys()) if k == "pystray" or k.startswith("pystray.")]
    for k in keys_to_evict:
        del sys.modules[k]
    try:
        return importlib.import_module("pystray")
    except Exception:
        # ImportError — pystray not installed.
        # Xlib.error.DisplayNameError — pystray installed but headless
        #   Linux box has no $DISPLAY (Xlib backend eagerly opens it).
        # Xlib.error.XlibError — other Xlib failure during backend probe.
        # Any other Exception — defensive: treat as "no usable pystray".
        return None
    finally:
        # Restore the autouse-mock so subsequent tests in this
        # pytest session still see the mocked pystray. If
        # ``saved`` was None (no mock installed — unlikely given
        # the autouse fixture, but defensive), leave the real
        # module in place; the fixture's teardown will restore the
        # pre-fixture state for the next test.
        if saved is not None:
            sys.modules["pystray"] = saved


@pytest.mark.skipif(
    sys.platform != "win32",
    reason=(
        "pystray sets the private `_icon_handle` instance attribute only in "
        "its Win32 backend (pystray/_win32.py); the darwin/Xorg backends "
        "never have it. The DestroyIcon workaround this test guards is a "
        "Win32-only bug workaround (tray.py:_apply_state), so the attribute "
        "can only be verified on Windows."
    ),
)
def test_pystray_icon_class_exposes_icon_handle():
    """Stopgap regression: assert ``pystray.Icon._icon_handle`` still exists.

    If this test fails after a ``pystray`` version bump, the private
    ``_icon_handle`` attribute was renamed or removed. Action
    items (see S2- / TODO S2-):

      * Replace the access in ``voice_typer/server/tray.py:_apply_state``
        with the new public API (file an upstream issue for
        ``reset_icon_handle()`` if one doesn't exist yet).
      * Update or replace this test to assert the new public API.
      * Bump ``pystray>=0.20`` (or whichever release exposes it)
        in ``pyproject.toml``.

    See the module docstring for full context.
    """
    pystray = _load_real_pystray()
    if pystray is None:
        # Real pystray isn't installed in this sandbox (e.g. Linux
        # CI without GUI deps). Nothing to introspect — skip
        # cleanly.
        pytest.skip("real pystray not installed in this environment — cannot introspect pystray.Icon._icon_handle")

    # The DestroyIcon workaround in ``tray.py:_apply_state`` writes
    # to ``self._icon._icon_handle = None`` on OSError — i.e. it
    # touches the INSTANCE attribute. pystray's platform backends set
    # ``_icon_handle`` in ``Icon.__init__`` (it is NOT a class
    # attribute), so a ``hasattr(pystray.Icon, ...)`` class-level check
    # is always False on current pystray. Construct a probe instance
    # and verify the attribute exists at runtime instead. On a headless
    # host where construction raises (Xlib backends open a display at
    # construction time), skip cleanly — same philosophy as the
    # import-failure skip above.
    try:
        probe_icon = pystray.Icon("probe")
    except Exception:
        pytest.skip("cannot construct pystray.Icon in this environment — cannot verify _icon_handle")
    assert hasattr(probe_icon, "_icon_handle"), (
        "pystray.Icon instances no longer expose the private `_icon_handle` "
        "attribute. The DestroyIcon workaround in "
        "voice_typer/server/tray.py:_apply_state is broken. See "
        "S2- / TODO S2-replace the private attribute "
        "access with a public `reset_icon_handle()` API and bump "
        "pystray to the release that exposes it."
    )


class _FakeIcon:
    """Plain-Python stand-in for ``pystray.Icon`` used by the fallback tests.

    Why not MagicMock? Because mutating ``type(mock).icon`` to install
    a raising property would mutate the *global* ``MagicMock`` class
    and leak into every other MagicMock in the test session. Using a
    dedicated subclass keeps the behavior local to this test module.

    Behavior:

      * ``icon = value`` (setter) always raises ``OSError`` —
        simulates the WinError 1402 that triggers the
        workaround branch in ``tray.py:_apply_state``.
      * ``_icon_handle`` is exposed as a property whose getter
        raises ``AttributeError`` when ``has_handle=False`` so
        ``hasattr(icon, "_icon_handle")`` returns False (mirrors
        a future pystray release that removed the attribute).
      * ``title`` is a plain attribute (the production code only
        sets it after the OSError branch — we don't need to
        exercise it).
    """

    def __init__(self, *, has_icon_handle: bool) -> None:
        self._has_icon_handle = has_icon_handle
        if has_icon_handle:
            self._icon_handle_value: object = "sentinel-handle-value"
        self.title: object = None

    # --- icon property ------------------------------------------------
    # Both getter and setter raise OSError so ``self._icon.icon =
    # _make_icon(state)`` enters the workaround branch. (Getter
    # raising is only defensive — the production code only sets.)
    @property
    def icon(self) -> object:
        raise OSError("simulated WinError 1402 (icon getter)")

    @icon.setter
    def icon(self, value: object) -> None:
        raise OSError("simulated WinError 1402 (icon setter)")

    # --- _icon_handle property ---------------------------------------
    # When ``has_icon_handle=False`` the getter raises
    # AttributeError — this is what makes ``hasattr(icon,
    # "_icon_handle")`` return False in the production guard.
    @property
    def _icon_handle(self) -> object:
        if not self._has_icon_handle:
            raise AttributeError("_icon_handle")
        return self._icon_handle_value

    @_icon_handle.setter
    def _icon_handle(self, value: object) -> None:
        if not self._has_icon_handle:
            raise AttributeError("_icon_handle")
        self._icon_handle_value = value


def _make_minimal_tray_with_icon(icon: object) -> TrayIcon:
    """Build a ``TrayIcon`` whose ``self._icon`` is ``icon``.

    We construct via ``__new__`` + manual attribute setup (mirroring
    the pattern in ``tests/test_tray_pending_drain.py`` and
    ``tests/tauri/test_tray_menu.py::_FakeTray``) so we don't invoke
    ``__init__`` — which would try to create a real ``pystray.Icon``
    and require an X display. Only the attributes referenced by
    ``_apply_state`` (directly + transitively via
    ``_compute_tooltip``) are set:

      * ``_icon``              — the fake icon (set by caller)
      * ``_state``             — current state
      * ``_recording_started_at`` — None (no active recording timer)
      * ``_cpu_fallback_active``   — False (SK-b flag in tooltip)
      * ``_config``            — None (TRAY-022 model-name source)
      * ``_hotkey``            — None (falls through to "<f2>" default)
      * ``_icon_lock``         — RLock (FR-23: _apply_state holds it)
      * ``_last_applied_state`` — None (AB-16/DJ-36 cache-skip read)
    """
    import threading

    tray = TrayIcon.__new__(TrayIcon)
    tray._icon = icon
    tray._state = AppState.IDLE
    tray._recording_started_at = None
    tray._cpu_fallback_active = False  # SK-b
    tray._config = None  # model name source
    tray._hotkey = None  # falls through to "<f2>" default
    # _apply_state now acquires _icon_lock around the icon-write pair.
    tray._icon_lock = threading.RLock()
    # _apply_state reads _last_applied_state for the cache-skip.
    tray._last_applied_state = None
    return tray


def test_apply_state_warns_when_icon_handle_missing(caplog):
    """Graceful fallback: ``_apply_state`` warns when workaround can't fire.

    Simulates a future pystray release (0.20+) that removed the
    private ``_icon_handle`` attribute: the fake icon raises
    ``OSError`` on icon assignment AND has no ``_icon_handle``
    attribute. ``_apply_state`` must:

      * NOT re-raise the OSError (the workaround must degrade
        gracefully, not crash the tray loop).
      * Log a WARNING so the silent workaround failure surfaces
        in diagnostics (S2- requirement: the failure mode
        must be visible to users / devs, not silently swallowed).

    The WARNING message must mention ``_icon_handle`` so a developer
    grepping logs for the symptom of a pystray-version-bump
    regression can find the root cause via the S2- /
    TODO S2- cross-reference.
    """
    icon = _FakeIcon(has_icon_handle=False)
    # Sanity check: the fake icon should report no ``_icon_handle``
    # via ``hasattr`` — this is what the production guard checks
    # before attempting to clear it. If this sanity check fails the
    # test fixture is wrong, not the production code.
    assert hasattr(icon, "_icon_handle") is False, (
        "test fixture broken: _FakeIcon(has_icon_handle=False) should make hasattr(icon, '_icon_handle') return False"
    )

    tray = _make_minimal_tray_with_icon(icon)

    # Must not raise — the OSError is caught and the missing
    # ``_icon_handle`` is handled by the graceful fallback
    # (log.warning + return).
    tray._apply_state(AppState.RECORDING, "recording")

    warning_records = [r for r in caplog.records if r.levelname == "WARNING"]
    assert any("_icon_handle" in r.getMessage() for r in warning_records), (
        "Expected a WARNING log mentioning `_icon_handle` when the "
        "DestroyIcon workaround can't fire (attribute missing). "
        "Got records: " + repr([(r.levelname, r.getMessage()) for r in caplog.records])
    )


def test_apply_state_clears_icon_handle_when_present(caplog):
    """Belt-and-braces: workaround fires when ``_icon_handle`` IS present.

    Verifies the existing  / GT-E1-8 workaround still works:
    when ``OSError`` is raised on icon assignment AND
    ``_icon_handle`` IS present, the workaround sets it to ``None``
    so pystray re-creates the icon handle on the next call. No
    warning should be logged in this case (the workaround is
    functioning normally — the warning is reserved for the
    missing-attribute fallback path).
    """
    icon = _FakeIcon(has_icon_handle=True)
    # Sanity check: the fake icon should report ``_icon_handle`` as
    # present so the workaround branch fires.
    assert hasattr(icon, "_icon_handle") is True, (
        "test fixture broken: _FakeIcon(has_icon_handle=True) should make hasattr(icon, '_icon_handle') return True"
    )

    tray = _make_minimal_tray_with_icon(icon)
    tray._apply_state(AppState.TRANSCRIBING, "transcribing")

    # The workaround must have cleared the handle.
    assert icon._icon_handle is None, (
        "Expected `_icon_handle` to be set to None after OSError "
        "( / GT-E1-8 workaround), but it is: " + repr(icon._icon_handle)
    )
    # And NO warning should have been logged (the workaround
    # functioned normally — the warning is reserved for the
    # missing-attribute fallback case).
    warning_records = [r for r in caplog.records if r.levelname == "WARNING"]
    assert not any("_icon_handle" in r.getMessage() for r in warning_records), (
        "Did not expect a WARNING log when the workaround fired "
        "normally (the warning is reserved for the missing-attribute "
        "fallback). Got warnings: " + repr([(r.levelname, r.getMessage()) for r in warning_records])
    )
