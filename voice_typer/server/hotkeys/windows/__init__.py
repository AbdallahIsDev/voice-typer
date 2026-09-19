"""Windows-native hotkey backend, split package."""

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
