"""Tests for ``voice_typer.server.hotkeys.windows.polling_strategy``.

The polling-strategy functions are module-level functions that take
``self`` as their first argument so they can be assigned as methods on
:class:`voice_typer.server.hotkeys.windows_native.WindowsNativeHotkey`
(Python's descriptor protocol passes the instance as ``self``). This
test module exercises them IN ISOLATION by binding them to a small
``_MockBackend`` and driving the polling loop through synthetic
key-state cycles — without depending on a Windows host or the heavy
``WindowsNativeHotkey.start()`` wiring.

The mock pattern mirrors ``tests/test_hotkeys_win32.py`` (mocking
``ctypes.windll`` + side-effects on ``GetAsyncKeyState``) but skips the
full ``WindowsNativeHotkey`` machinery so the polling edge cases can be
pinned directly against ``polling_strategy.py``:

  (a) single press → single callback invocation
  (b) press-hold-release → exactly one callback (no repeat)
  (c) caps-lock / IME composition suppression
  (d) poll-interval backoff (Sleep(1) during caps-lock suppression,
      Sleep(50) during IME composition, Sleep(8) otherwise)
  (e) graceful degradation when Win32 API unavailable (``_user32=None``)
"""

from __future__ import annotations

import ctypes
import threading
import time
from unittest.mock import MagicMock

import pytest
from voice_typer.server.hotkeys.win32_vk import (
    _MOD_ALT,
    _VK_CAPITAL,
    _VK_MENU,
)
from voice_typer.server.hotkeys.windows import polling_strategy

from tests.fixtures.wait_for import wait_for

# ---------------------------------------------------------------------------
# Mock backend — binds the polling_strategy functions as methods,
# mimicking how ``WindowsNativeHotkey`` binds them at class body.
# ---------------------------------------------------------------------------


class _MockBackend:
    """Minimal host class for the ``polling_strategy`` functions.

    The polling-strategy functions read ``self._user32``,
    ``self._kernel32``, ``self._modifiers``, ``self._vk``,
    ``self._stop_event``, ``self._on_release_callback``,
    ``self._is_modifier_only``, ``self._caps_lock_suppressing``,
    ``self._last_nonmod_check_time``, ``self._last_nonmod_pressed``,
    ``self._toggle_on_keyup``, and call helper methods
    (``_is_ime_composing_throttled``, ``_modifiers_pressed``,
    ``_other_modifiers_pressed``, ``_key_pressed``,
    ``_any_non_modifier_key_pressed_throttled``,
    ``_suppress_caps_lock_toggle``, ``_ensure_caps_lock_off``).

    Tests set the attributes they need and override the helper methods
    with simple stubs (or bind the real ``polling_strategy`` ones).
    """

    # Bind the real polling_strategy functions as methods (descriptor
    # protocol passes the instance as ``self``).
    _run_polling_loop = polling_strategy.run_polling_loop
    _run_modifier_only_polling_loop = polling_strategy.run_modifier_only_polling_loop
    _key_pressed = polling_strategy.key_pressed
    _modifiers_pressed = polling_strategy.modifiers_pressed
    _other_modifiers_pressed = polling_strategy.other_modifiers_pressed
    _is_altgr_pressed = polling_strategy.is_altgr_pressed
    _any_non_modifier_key_pressed = polling_strategy.any_non_modifier_key_pressed
    _any_non_modifier_key_pressed_throttled = polling_strategy.any_non_modifier_key_pressed_throttled

    def __init__(self):
        self._user32 = None
        self._kernel32 = None
        self._modifiers = 0
        self._vk = None
        self._stop_event = threading.Event()
        self._on_release_callback = None
        self._is_modifier_only = False
        self._caps_lock_suppressing = False
        self._last_nonmod_check_time = 0.0
        self._last_nonmod_pressed = False
        self._toggle_on_keyup = False
        # Default: IME is never composing; tests override.
        self._is_ime_composing_throttled = lambda: False
        # Caps-lock suppression stubs — defaulted to no-ops; tests
        # override to assert they were called.
        self._suppress_caps_lock_toggle = MagicMock()
        self._ensure_caps_lock_off = MagicMock()


@pytest.fixture()
def mock_windll(monkeypatch):
    """Patch ``ctypes.windll`` with a MagicMock (Linux has no windll).

    Returns the mock_windll so tests can reach in and configure
    ``user32``, ``kernel32``, ``winmm`` individually.
    """
    mock_windll = MagicMock()
    mock_windll.user32 = MagicMock()
    mock_windll.kernel32 = MagicMock()
    mock_windll.winmm = MagicMock()
    # Default: GetAsyncKeyState returns 0 (no key pressed).
    mock_windll.user32.GetAsyncKeyState.return_value = 0
    monkeypatch.setattr(ctypes, "windll", mock_windll, raising=False)
    return mock_windll


def _make_backend(mock_windll, **kwargs):
    """Build a ``_MockBackend`` wired to the patched ``mock_windll``.

    The backend's ``_user32`` and ``_kernel32`` point at the mocked
    DLLs so the polling loop reads/writes the mocks. Additional keyword
    arguments override default backend attributes (e.g. ``_vk=0x71``).
    """
    backend = _MockBackend()
    backend._user32 = mock_windll.user32
    backend._kernel32 = mock_windll.kernel32
    for key, value in kwargs.items():
        setattr(backend, key, value)
    return backend


def _run_in_thread_and_join(target, args=(), timeout=2.0):
    """Run ``target`` in a daemon thread and join with a timeout.

    Used to run the polling loop (which spins until ``_stop_event`` is
    set) without blocking the test thread — the test thread flips state
    and the stop flag from outside.
    """
    thread = threading.Thread(target=target, args=args, daemon=True)
    thread.start()
    return thread


def _wait_until(predicate, timeout: float = 3.0, msg: str = "condition not met"):
    """Poll ``predicate`` until truthy or ``timeout`` elapses.

    Thin wrapper around :func:`tests.fixtures.wait_for.wait_for` that
    raises ``AssertionError`` on timeout. Used for positive waits
    (waiting for a callback to fire or a counter to advance).
    """
    if not wait_for(predicate, timeout=timeout):
        raise AssertionError(f"{msg} (waited {timeout}s)")


# ---------------------------------------------------------------------------
# (e) Graceful degradation when Win32 API unavailable (``_user32=None``)
# ---------------------------------------------------------------------------


class TestGracefulDegradation:
    """The stateless key-state helpers must return ``False`` (not raise)
    when ``self._user32`` is ``None``. This is the non-Windows / no-Win32
    fallback path: the hotkey listener is constructed lazily on Windows
    only, but tests exercise the polling path with ``_user32=None`` to
    verify the helpers don't crash on a non-Windows host."""

    def test_key_pressed_returns_false_when_user32_none(self):
        backend = _MockBackend()
        backend._user32 = None
        assert backend._key_pressed(_VK_MENU) is False

    def test_modifiers_pressed_returns_false_when_user32_none(self):
        backend = _MockBackend()
        backend._user32 = None
        backend._modifiers = _MOD_ALT
        assert backend._modifiers_pressed() is False

    def test_other_modifiers_pressed_returns_false_when_user32_none(self):
        backend = _MockBackend()
        backend._user32 = None
        backend._modifiers = _MOD_ALT
        assert backend._other_modifiers_pressed() is False

    def test_is_altgr_pressed_returns_false_when_user32_none(self):
        backend = _MockBackend()
        backend._user32 = None
        assert backend._is_altgr_pressed() is False

    def test_any_non_modifier_key_pressed_returns_false_when_user32_none(self):
        backend = _MockBackend()
        backend._user32 = None
        assert backend._any_non_modifier_key_pressed(frozenset()) is False

    def test_any_non_modifier_key_pressed_throttled_returns_false_when_user32_none(
        self,
    ):
        """The throttled wrapper delegates to the underlying scan, which
        returns False on a non-Windows host. The wrapper must NOT raise
        even though ``_last_nonmod_check_time`` is 0.0 (uninitialized)."""
        backend = _MockBackend()
        backend._user32 = None
        backend._last_nonmod_pressed = False
        backend._last_nonmod_check_time = 0.0
        assert backend._any_non_modifier_key_pressed_throttled(frozenset()) is False


# ---------------------------------------------------------------------------
# (a) Single press → single callback (toggle mode, no on_release)
# ---------------------------------------------------------------------------


class TestSinglePressSingleCallback:
    """A single not-held → held → not-held cycle must fire the callback
    exactly once in toggle mode (no on_release, no toggle_on_keyup).

    The polling loop fires the callback on the not-held → held
    transition (``is_pressed and not was_pressed``) and never on the
    held → not-held transition in toggle mode.
    """

    def test_single_press_fires_callback_once(self, mock_windll):
        backend = _make_backend(mock_windll, _vk=0x71, _modifiers=0)
        mock_user32 = mock_windll.user32

        # State: 0 = up, 1 = held, 2 = released.
        state = {"value": 0}

        def fake_get_async_key_state(vk):
            if vk != 0x71:  # VK_F2
                return 0
            return 0x8000 if state["value"] == 1 else 0

        mock_user32.GetAsyncKeyState.side_effect = fake_get_async_key_state

        callback = MagicMock()
        thread = _run_in_thread_and_join(backend._run_polling_loop, args=(callback,))
        try:
            # Phase 1: nothing pressed. Wait 30ms and verify the
            # callback does NOT fire (wait_for returns True if the
            # predicate became truthy — we expect False here).
            assert not wait_for(lambda: callback.call_count > 0, timeout=0.03), "Callback fired before key was pressed"
            state["value"] = 1  # phase 2: press
            # Wait for the callback to fire on the not-held → held
            # transition (toggle mode fires on press).
            _wait_until(
                lambda: callback.call_count >= 1,
                timeout=2.0,
                msg="Callback did not fire on press",
            )
            assert callback.call_count == 1, f"Expected exactly one callback on press, got {callback.call_count}"
            state["value"] = 2  # phase 3: release
            # Toggle mode must NOT fire on release. Wait 40ms and
            # verify the callback count stays at 1 (wait_for returns
            # True if the predicate became truthy — we expect False).
            _count_after_press = callback.call_count
            assert not wait_for(
                lambda: callback.call_count > _count_after_press,
                timeout=0.04,
            ), f"Callback fired on release in toggle mode: {callback.call_count}"
        finally:
            backend._stop_event.set()
            thread.join(timeout=2.0)


# ---------------------------------------------------------------------------
# (b) Press-hold-release → one callback (no repeat)
# ---------------------------------------------------------------------------


class TestPressHoldReleaseNoRepeat:
    """Holding the key for an extended period must NOT re-fire the
    callback. The polling loop fires exactly once on the not-held →
    held transition; subsequent iterations where the key stays held
    must NOT fire again (``is_pressed and was_pressed`` → no fire)."""

    def test_hold_does_not_repeat_callback(self, mock_windll):
        backend = _make_backend(mock_windll, _vk=0x71, _modifiers=0)
        mock_user32 = mock_windll.user32

        # State: 0 = up, 1 = held (for an extended period), 2 = released.
        state = {"value": 0}

        def fake_get_async_key_state(vk):
            if vk != 0x71:
                return 0
            return 0x8000 if state["value"] == 1 else 0

        mock_user32.GetAsyncKeyState.side_effect = fake_get_async_key_state

        callback = MagicMock()
        thread = _run_in_thread_and_join(backend._run_polling_loop, args=(callback,))
        try:
            # Phase 1: nothing pressed. Wait 30ms and verify no fire.
            assert not wait_for(lambda: callback.call_count > 0, timeout=0.03)
            state["value"] = 1  # press and HOLD
            # Wait for the callback to fire on the not-held → held
            # transition (toggle mode fires on press).
            _wait_until(
                lambda: callback.call_count >= 1,
                timeout=2.0,
                msg="Callback did not fire on press",
            )
            # Hold for 150ms — must NOT re-fire. Wait 150ms and verify
            # the callback count stays at 1 (wait_for returns True if
            # the predicate became truthy — we expect False here).
            _count_after_press = callback.call_count
            assert not wait_for(
                lambda: callback.call_count > _count_after_press,
                timeout=0.15,
            ), (
                f"Callback fired {callback.call_count} times during hold — "
                f"must fire exactly once on press, never while held"
            )
            state["value"] = 2  # release
            # Toggle mode must NOT fire on release. Wait 40ms and verify.
            _count_after_release = callback.call_count
            assert not wait_for(
                lambda: callback.call_count > _count_after_release,
                timeout=0.04,
            ), f"Callback fired on release in toggle mode: {callback.call_count}"
        finally:
            backend._stop_event.set()
            thread.join(timeout=2.0)

    def test_two_press_cycles_fire_exactly_twice(self, mock_windll):
        """Two independent press/release cycles must produce exactly
        two callback invocations (one per press)."""
        backend = _make_backend(mock_windll, _vk=0x71, _modifiers=0)
        mock_user32 = mock_windll.user32

        state = {"value": 0}

        def fake_get_async_key_state(vk):
            if vk != 0x71:
                return 0
            return 0x8000 if state["value"] == 1 else 0

        mock_user32.GetAsyncKeyState.side_effect = fake_get_async_key_state

        callback = MagicMock()
        thread = _run_in_thread_and_join(backend._run_polling_loop, args=(callback,))
        try:
            # Wait for the loop's HOTKEY-DEFER-001 seed (the first
            # GetAsyncKeyState call) to read the key state BEFORE the
            # first press. The key is still UP at this point, so
            # ``was_pressed`` seeds False and the first press below is
            # a genuine not-held → held transition. Without this wait,
            # the seed can race the press on a loaded CI box: the seed
            # reads the already-held key, suppresses the first press
            # (HOTKEY-DEFER-001), and the cycle-1 wait times out
            # ("Callback did not fire on press (cycle 1)").
            _wait_until(
                lambda: mock_user32.GetAsyncKeyState.call_count >= 1,
                timeout=2.0,
                msg="Polling loop seed did not read the key state",
            )
            for i in range(1, 3):
                state["value"] = 1
                # Wait for the callback to fire on the press transition.
                _wait_until(
                    lambda _i=i: callback.call_count == _i,
                    timeout=2.0,
                    msg=f"Callback did not fire on press (cycle {i})",
                )
                state["value"] = 0
                # Toggle mode must NOT fire on release. Wait 40ms and
                # verify the callback count stays at i.
                _count_after_press = callback.call_count
                assert not wait_for(
                    lambda _count=_count_after_press: callback.call_count > _count,
                    timeout=0.04,
                ), f"Callback fired on release in toggle mode: {callback.call_count}"
            assert callback.call_count == 2, f"Expected 2 fires (one per press), got {callback.call_count}"
        finally:
            backend._stop_event.set()
            thread.join(timeout=2.0)


# ---------------------------------------------------------------------------
# (c) Caps-lock / IME composition suppression
# ---------------------------------------------------------------------------


class TestImeCompositionSuppression:
    """When IME composition is active, the polling loop must skip
    key-down processing entirely (so synthetic IME keystrokes don't
    fire the hotkey callback). The loop also resets ``was_pressed`` so
    a stray IME composition doesn't leak into the next press cycle."""

    def test_ime_composing_skips_callback(self, mock_windll):
        backend = _make_backend(mock_windll, _vk=0x71, _modifiers=0)
        mock_user32 = mock_windll.user32
        # IME is composing the entire time.
        backend._is_ime_composing_throttled = lambda: True
        # Even though GetAsyncKeyState says the key is held...
        mock_user32.GetAsyncKeyState.return_value = 0x8000

        callback = MagicMock()
        thread = _run_in_thread_and_join(backend._run_polling_loop, args=(callback,))
        try:
            # IME is composing the entire time. Wait 100ms and verify
            # the callback does NOT fire (wait_for returns True if the
            # predicate became truthy — we expect False here).
            assert not wait_for(lambda: callback.call_count > 0, timeout=0.1), (
                f"Callback fired during IME composition: {callback.call_count}"
            )
        finally:
            backend._stop_event.set()
            thread.join(timeout=2.0)


class TestCapsLockSuppression:
    """When the hotkey is Caps Lock (VK=0x14), the polling loop calls
    ``_suppress_caps_lock_toggle`` on the press transition to undo the
    OS-level caps-state toggle, and skips processing entirely while
    ``_caps_lock_suppressing`` is True (so the synthetic keypress
    doesn't re-trigger the callback)."""

    def test_caps_lock_press_invokes_suppress_toggle(self, mock_windll):
        backend = _make_backend(mock_windll, _vk=_VK_CAPITAL, _modifiers=0)
        mock_user32 = mock_windll.user32

        # State: 0 = up, 1 = held. The seed call returns 0 (not pressed)
        # so was_pressed starts False; subsequent calls return 0x8000
        # (pressed) to simulate the user pressing Caps Lock.
        call_counter = {"n": 0}

        def fake_get_async_key_state(vk):
            if vk != _VK_CAPITAL:
                return 0
            n = call_counter["n"]
            call_counter["n"] += 1
            # First call (seed) returns 0; all others return "pressed".
            return 0 if n == 0 else 0x8000

        mock_user32.GetAsyncKeyState.side_effect = fake_get_async_key_state

        callback = MagicMock()
        thread = _run_in_thread_and_join(backend._run_polling_loop, args=(callback,))
        try:
            # Wait for the polling loop to observe the press and fire
            # the callback (the not-held → held transition).
            _wait_until(
                lambda: callback.call_count >= 1,
                timeout=2.0,
                msg="Caps Lock press did not fire the callback",
            )
            assert callback.call_count >= 1, "Caps Lock press should fire the callback"
            # _suppress_caps_lock_toggle must be called to undo the OS toggle.
            assert backend._suppress_caps_lock_toggle.called, "Caps Lock press must invoke _suppress_caps_lock_toggle"
        finally:
            backend._stop_event.set()
            thread.join(timeout=2.0)

    def test_caps_lock_suppressing_flag_skips_processing(self, mock_windll):
        """While ``_caps_lock_suppressing`` is True, the polling loop
        must skip the key-state check entirely (so the synthetic
        keypress doesn't re-trigger the callback or prematurely fire
        on_release)."""
        backend = _make_backend(
            mock_windll,
            _vk=_VK_CAPITAL,
            _modifiers=0,
            _caps_lock_suppressing=True,
        )
        mock_user32 = mock_windll.user32
        # Pretend the key is "pressed" — but suppression should skip
        # the press-processing branch entirely.
        mock_user32.GetAsyncKeyState.return_value = 0x8000

        callback = MagicMock()
        thread = _run_in_thread_and_join(backend._run_polling_loop, args=(callback,))
        try:
            # While _caps_lock_suppressing is True, the polling loop
            # skips processing. Wait 80ms and verify the callback does
            # NOT fire (wait_for returns True if the predicate became
            # truthy — we expect False here).
            assert not wait_for(lambda: callback.call_count > 0, timeout=0.08), (
                f"Callback fired during caps-lock suppression: {callback.call_count}"
            )
        finally:
            backend._stop_event.set()
            thread.join(timeout=2.0)


# ---------------------------------------------------------------------------
# (d) Poll-interval backoff (Sleep cadence)
# ---------------------------------------------------------------------------


class TestPollIntervalBackoff:
    """The polling loop uses different ``Sleep`` durations depending on
    the loop state:

    - Normal polling: ``Sleep(8)`` (~125 Hz) — the default cadence.
    - IME composition: ``Sleep(50)`` — back off while the user is
      typing a composed character.
    - Caps-lock suppression: ``Sleep(1)`` — brief transient that needs
      sub-8ms latency so the suppression flag is observed quickly.

    These assertions pin the battery-drain / CPU-backoff fix so a
    future refactor can't silently revert to ``Sleep(1)`` everywhere
    (which would spin at 1000 Hz) or ``Sleep(50)`` everywhere (which
    would add 50ms hotkey latency)."""

    def test_normal_polling_uses_sleep_8(self, mock_windll):
        backend = _make_backend(mock_windll, _vk=0x71, _modifiers=0)
        mock_user32 = mock_windll.user32
        mock_kernel32 = mock_windll.kernel32
        backend._is_ime_composing_throttled = lambda: False
        backend._caps_lock_suppressing = False
        mock_user32.GetAsyncKeyState.return_value = 0  # never pressed

        thread = _run_in_thread_and_join(backend._run_polling_loop, args=(MagicMock(),))
        try:
            # Wait for the polling loop to make at least 2 Sleep calls
            # (confirms the loop ran at least 2 iterations at 8ms each).
            _wait_until(
                lambda: mock_kernel32.Sleep.call_count >= 2,
                timeout=2.0,
                msg="Polling loop did not make enough Sleep calls",
            )
        finally:
            backend._stop_event.set()
            thread.join(timeout=2.0)

        sleep_calls = [c.args[0] for c in mock_kernel32.Sleep.call_args_list]
        assert 8 in sleep_calls, f"Expected Sleep(8) in normal polling, got {sleep_calls}"
        assert 1 not in sleep_calls, (
            f"Sleep(1) regression — should only be used during caps-lock suppression: {sleep_calls}"
        )
        assert 50 not in sleep_calls, (
            f"Sleep(50) regression — should only be used during IME composition: {sleep_calls}"
        )

    def test_ime_composition_uses_sleep_50(self, mock_windll):
        backend = _make_backend(mock_windll, _vk=0x71, _modifiers=0)
        mock_user32 = mock_windll.user32
        mock_kernel32 = mock_windll.kernel32
        backend._is_ime_composing_throttled = lambda: True
        mock_user32.GetAsyncKeyState.return_value = 0x8000

        thread = _run_in_thread_and_join(backend._run_polling_loop, args=(MagicMock(),))
        try:
            # Wait for the polling loop to make at least 2 Sleep calls
            # (confirms the loop ran at least 2 iterations at 50ms each).
            _wait_until(
                lambda: mock_kernel32.Sleep.call_count >= 2,
                timeout=2.0,
                msg="Polling loop did not make enough Sleep calls during IME composition",
            )
        finally:
            backend._stop_event.set()
            thread.join(timeout=2.0)

        sleep_calls = [c.args[0] for c in mock_kernel32.Sleep.call_args_list]
        assert 50 in sleep_calls, f"Expected Sleep(50) during IME composition, got {sleep_calls}"

    def test_caps_lock_suppression_uses_sleep_1(self, mock_windll):
        backend = _make_backend(
            mock_windll,
            _vk=_VK_CAPITAL,
            _modifiers=0,
            _caps_lock_suppressing=True,
        )
        mock_user32 = mock_windll.user32
        mock_kernel32 = mock_windll.kernel32
        mock_user32.GetAsyncKeyState.return_value = 0

        thread = _run_in_thread_and_join(backend._run_polling_loop, args=(MagicMock(),))
        try:
            # Wait for the polling loop to make at least 2 Sleep calls
            # (confirms the loop ran at least 2 iterations at 1ms each).
            _wait_until(
                lambda: mock_kernel32.Sleep.call_count >= 2,
                timeout=2.0,
                msg="Polling loop did not make enough Sleep calls during caps-lock suppression",
            )
        finally:
            backend._stop_event.set()
            thread.join(timeout=2.0)

        sleep_calls = [c.args[0] for c in mock_kernel32.Sleep.call_args_list]
        # All sleeps during caps-lock suppression must be 1ms (the
        # brief-transient sub-8ms latency path).
        assert sleep_calls, "Expected at least one Sleep call during caps-lock suppression"
        assert all(s == 1 for s in sleep_calls), f"Expected Sleep(1) during caps-lock suppression, got {sleep_calls}"


# ---------------------------------------------------------------------------
# Modifier-only polling loop — PTT press-and-hold doesn't repeat
# ---------------------------------------------------------------------------


class TestModifierOnlyPollingLoop:
    """The modifier-only polling loop (``_run_modifier_only_polling_loop``)
    fires the press callback exactly once on the not-held → held
    transition (PTT mode) and never re-fires while the modifier stays
    held. Mirrors the test_hotkeys_win32.py pattern but exercises the
    function in isolation."""

    def test_ptt_press_fires_once_hold_does_not_repeat(self, mock_windll):
        backend = _make_backend(mock_windll, _modifiers=_MOD_ALT)
        mock_user32 = mock_windll.user32

        state = {"value": 0}

        def fake_get_async_key_state(vk):
            if vk != _VK_MENU:  # VK_MENU (Alt) = 0x12
                return 0
            return 0x8000 if state["value"] == 1 else 0

        mock_user32.GetAsyncKeyState.side_effect = fake_get_async_key_state

        press_callback = MagicMock()
        release_callback = MagicMock()
        backend._on_release_callback = release_callback

        thread = _run_in_thread_and_join(backend._run_modifier_only_polling_loop, args=(press_callback,))
        try:
            # Phase 1: nothing pressed. Wait 30ms and verify no fire.
            assert not wait_for(lambda: press_callback.call_count > 0, timeout=0.03)
            assert not wait_for(lambda: release_callback.call_count > 0, timeout=0.03)
            state["value"] = 1  # press
            # Wait for the press callback to fire (PTT mode fires on press).
            _wait_until(
                lambda: press_callback.call_count >= 1,
                timeout=2.0,
                msg="PTT press callback did not fire on press",
            )
            assert press_callback.call_count == 1, (
                f"PTT press should fire once on press, got {press_callback.call_count}"
            )
            # Hold for 150ms — must NOT re-fire. Wait 150ms and verify
            # the press callback count stays at 1 (wait_for returns
            # True if the predicate became truthy — we expect False).
            _press_count_after_hold_start = press_callback.call_count
            assert not wait_for(
                lambda: press_callback.call_count > _press_count_after_hold_start,
                timeout=0.15,
            ), f"PTT press fired {press_callback.call_count} times during hold — must fire exactly once"
            assert release_callback.call_count == 0
            state["value"] = 0  # release
            # Wait for the release callback to fire.
            _wait_until(
                lambda: release_callback.call_count >= 1,
                timeout=2.0,
                msg="PTT on_release did not fire on release",
            )
            assert release_callback.call_count == 1, (
                f"PTT on_release should fire once on release, got {release_callback.call_count}"
            )
            assert press_callback.call_count == 1  # unchanged
        finally:
            backend._stop_event.set()
            thread.join(timeout=2.0)


# ---------------------------------------------------------------------------
# Stateful helpers — throttled non-modifier key scan caching
# ---------------------------------------------------------------------------


class TestAnyNonModifierKeyPressedThrottled:
    """The throttled wrapper caches False results for 50ms to avoid
    re-scanning 248 VK codes on every 8ms polling iteration. True
    results are NOT cached across releases (so the next press cycle
    re-scans fresh)."""

    def test_false_result_cached_within_50ms(self, mock_windll):
        backend = _make_backend(mock_windll, _modifiers=0)
        mock_user32 = mock_windll.user32
        # No non-modifier key pressed — every VK returns 0.
        mock_user32.GetAsyncKeyState.return_value = 0
        backend._last_nonmod_pressed = False
        backend._last_nonmod_check_time = time.monotonic()  # fresh timestamp

        modifier_vks = frozenset({_VK_MENU})
        # First call scans and caches False.
        result1 = backend._any_non_modifier_key_pressed_throttled(modifier_vks)
        assert result1 is False
        scan_count_after_first = mock_user32.GetAsyncKeyState.call_count
        # Second call within 50ms — should NOT re-scan (cache hit).
        result2 = backend._any_non_modifier_key_pressed_throttled(modifier_vks)
        assert result2 is False
        scan_count_after_second = mock_user32.GetAsyncKeyState.call_count
        assert scan_count_after_second == scan_count_after_first, (
            "Throttled wrapper should NOT re-scan within 50ms of a False result"
        )

    def test_true_result_not_cached(self, mock_windll):
        """When the underlying scan returns True, the wrapper must NOT
        cache it — the next call within 50ms must re-scan fresh. This
        prevents a cached True from leaking into the next press cycle
        (which would wrongly suppress the fire)."""
        backend = _make_backend(mock_windll, _modifiers=0)
        mock_user32 = mock_windll.user32

        # Pretend a non-modifier key (e.g. 'C' = 0x43) is held.
        def fake_get_async_key_state(vk):
            return 0x8000 if vk == 0x43 else 0

        mock_user32.GetAsyncKeyState.side_effect = fake_get_async_key_state
        backend._last_nonmod_pressed = False
        # Set the cache timestamp >50ms in the past so the first call
        # bypasses the False-cache and actually scans.
        backend._last_nonmod_check_time = time.monotonic() - 0.1

        modifier_vks = frozenset({_VK_MENU})
        # First call scans, finds 'C' pressed, returns True.
        result1 = backend._any_non_modifier_key_pressed_throttled(modifier_vks)
        assert result1 is True
        scan_count_after_first = mock_user32.GetAsyncKeyState.call_count
        # Second call within 50ms — must re-scan (True is NOT cached).
        result2 = backend._any_non_modifier_key_pressed_throttled(modifier_vks)
        assert result2 is True
        assert mock_user32.GetAsyncKeyState.call_count > scan_count_after_first, (
            "Throttled wrapper must re-scan after a True result (True is not cached)"
        )
