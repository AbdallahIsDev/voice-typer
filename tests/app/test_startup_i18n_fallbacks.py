"""Startup i18n fallback registration.

Pins the contract that the English fallback labels for the app-startup
i18n keys (``error.config_load_failed.*`` / ``state.app.starting``) are
registered by ``_register_startup_i18n_fallbacks()`` at app-INIT time
(called from ``VoiceTyperApp.__init__``) — NOT at module import time.
Importing ``voice_typer.server.app`` must stay side-effect-free with
respect to the i18n registry.
"""

from __future__ import annotations

import threading

from voice_typer.server import app as app_module, i18n

_STARTUP_KEYS = {
    "error.config_load_failed.title": "Config load failed",
    "error.config_load_failed.body": "Settings were reset to defaults. Check the logs for details.",
    "state.app.starting": "Starting...",
}


def _pop_startup_keys() -> dict[str, str]:
    """Remove the startup keys from the English registry (under the
    registry lock) and return the removed values."""
    removed: dict[str, str] = {}
    with i18n._LOCK:
        en = i18n._REGISTRY.setdefault("en", {})
        for key in _STARTUP_KEYS:
            value = en.pop(key, None)
            if value is not None:
                removed[key] = value
    return removed


def test_module_import_does_not_register_fallbacks():
    """Importing ``app`` must not (re)populate the startup keys: the
    registration moved from module level into ``__init__``. The keys
    may legitimately already exist (``i18n._INITIAL_LABELS`` owns the
    canonical English fallbacks), so assert on the MECHANISM: no
    module-level ``with i18n._LOCK`` registration block remains at
    import scope — the only registrant is the helper function.
    """
    import ast
    import inspect

    source = inspect.getsource(app_module)
    tree = ast.parse(source)
    module_level_locks = [
        node for node in tree.body if isinstance(node, (ast.With, ast.AsyncWith)) and "i18n._LOCK" in ast.dump(node)
    ]
    assert module_level_locks == [], "i18n registry mutation must not run at module import time"
    assert hasattr(app_module, "_register_startup_i18n_fallbacks"), (
        "the registration helper must exist on the app module"
    )


def test_helper_registers_missing_english_fallbacks():
    """After the keys are removed, one helper call restores all three
    English fallbacks."""
    removed = _pop_startup_keys()
    try:
        # Sanity: the keys are really gone (or were never present).
        app_module._register_startup_i18n_fallbacks()
        with i18n._LOCK:
            en = i18n._REGISTRY.setdefault("en", {})
            for key, expected in _STARTUP_KEYS.items():
                assert en.get(key) == expected, f"{key} must be registered"
    finally:
        # Restore whatever the process had before (setdefault contract).
        with i18n._LOCK:
            en = i18n._REGISTRY.setdefault("en", {})
            for key, value in removed.items():
                en.setdefault(key, value)


def test_helper_is_idempotent_and_thread_safe():
    """Calling the helper repeatedly (including concurrently, as happens
    when several app instances are constructed in tests) must never
    overwrite an existing value and must not raise."""
    results: list[Exception | None] = []

    def _call() -> None:
        try:
            app_module._register_startup_i18n_fallbacks()
            results.append(None)
        except Exception as exc:  # pragma: no cover — surfaced below
            results.append(exc)

    threads = [threading.Thread(target=_call) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)
    assert results == [None] * 4
    with i18n._LOCK:
        en = i18n._REGISTRY.setdefault("en", {})
        for key, expected in _STARTUP_KEYS.items():
            assert en.get(key) == expected
