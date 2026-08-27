"""Windows-native hotkey backend — split package.

Extracted from the original ``windows_native.py`` god-class.
Each module owns one concern:

- :mod:`.context` — Win32 ctypes argtypes/restype setup and the
  stateless ``compute_modifier_vks`` helper.
- :mod:`.ime_guard` — IME composition detection
  (``is_ime_composing`` / ``is_ime_composing_throttled``).
- :mod:`.caps_lock_suppressor` — reactive and proactive
  CapsLock-toggle suppression.
- :mod:`.polling_strategy` — the GetAsyncKeyState polling loop
  (``run_polling_loop``) and the modifier-only polling loop
  (``run_modifier_only_polling_loop``), plus the small key-state
  helpers (``modifiers_pressed``, ``other_modifiers_pressed``,
  ``is_altgr_pressed``, ``key_pressed``,
  ``any_non_modifier_key_pressed[_throttled]``).
- :mod:`.message_loop_strategy` — the WM_HOTKEY / LL-hook message
  pump (``run_message_loop``).
- :mod:`.ll_hook_strategy` — the WH_KEYBOARD_LL hook installer
  (``install_low_level_hook``) and the bounded-queue callback
  worker (``start_hook_callback_worker`` /
  ``enqueue_hook_callback``).

The :class:`voice_typer.server.hotkeys.windows_native.WindowsNativeHotkey`
class binds these strategy functions as methods — Python's
descriptor protocol passes the instance as ``self``, and
``inspect.getsource`` follows the function object's
``__code__.co_filename`` back to the strategy module, so
source-inspection regression tests still pin the polling-loop
implementation in :mod:`.polling_strategy`.
"""

from __future__ import annotations

from .caps_lock_suppressor import ensure_caps_lock_off, suppress_caps_lock_toggle
from .context import (
    compute_modifier_vks,
    setup_ll_hook_argtypes,
    setup_main_argtypes,
    setup_message_pump_argtypes,
)
from .ime_guard import is_ime_composing, is_ime_composing_throttled
from .ll_hook_strategy import (
    enqueue_hook_callback,
    install_low_level_hook,
    start_hook_callback_worker,
)
from .message_loop_strategy import run_message_loop
from .polling_strategy import (
    any_non_modifier_key_pressed,
    any_non_modifier_key_pressed_throttled,
    is_altgr_pressed,
    key_pressed,
    modifiers_pressed,
    other_modifiers_pressed,
    run_modifier_only_polling_loop,
    run_polling_loop,
)

__all__ = [
    "compute_modifier_vks",
    "ensure_caps_lock_off",
    "enqueue_hook_callback",
    "install_low_level_hook",
    "is_altgr_pressed",
    "any_non_modifier_key_pressed",
    "any_non_modifier_key_pressed_throttled",
    "is_ime_composing",
    "is_ime_composing_throttled",
    "key_pressed",
    "modifiers_pressed",
    "other_modifiers_pressed",
    "run_message_loop",
    "run_modifier_only_polling_loop",
    "run_polling_loop",
    "setup_ll_hook_argtypes",
    "setup_main_argtypes",
    "setup_message_pump_argtypes",
    "start_hook_callback_worker",
    "suppress_caps_lock_toggle",
]
