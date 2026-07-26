"""Unit tests for ``voice_typer/server/i18n.py`` (XS-96).

Server-side i18n module providing ``t(key, **fmt)`` translation with
locale switching, English fallback, format interpolation failure
tolerance, and thread-safe registry mutation.

Scope: this file tests ONLY ``voice_typer/server/i18n.py`` — the
server-side notification / state-message translator. The pre-existing
``tests/test_i18n_completeness.py`` tests the CLIENT-side i18n JSON
files at ``voice_typer/client/src/renderer/src/i18n/translations/en.json``
(a different module).

Test cases (per XS-96):
- (a) ``t(key, **fmt)`` with a valid key + placeholders.
- (b) ``register_locale`` + ``set_locale`` + ``get_locale`` round-trip.
- (c) Fallback chain: missing key in active locale → English → raw key.
- (d) Format interpolation failure (missing placeholder) returns
  unformatted text rather than raising.
- (e) Thread-safety: concurrent ``register_locale`` + ``t`` calls do
  not raise or corrupt state.
"""

from __future__ import annotations

import threading

import pytest
from voice_typer.server import i18n
from voice_typer.server.i18n import (
    get_locale,
    register_locale,
    set_locale,
    t,
)


@pytest.fixture(autouse=True)
def _restore_i18n_state():
    """Snapshot/restore the module-level registry + current locale.

    ``i18n`` is stateful (module-level ``_REGISTRY`` and
    ``_CURRENT_LOCALE``). Without this fixture, ``register_locale`` /
    ``set_locale`` calls in one test would leak into the next, making
    test order matter. The fixture captures the registry dict-by-ref
    snapshot and the locale string before each test, then restores
    them after — so each test sees a clean ``en``-only registry.
    """
    with i18n._LOCK:
        saved_registry = {loc: dict(labels) for loc, labels in i18n._REGISTRY.items()}
        saved_locale = i18n._CURRENT_LOCALE
    try:
        yield
    finally:
        with i18n._LOCK:
            i18n._REGISTRY.clear()
            i18n._REGISTRY.update(saved_registry)
            i18n._CURRENT_LOCALE = saved_locale


# ── (a) t(key, **fmt) with valid key + placeholders ─────────────────────


class TestTranslate:
    """``t(key, **fmt)`` — translate a key with optional format interpolation."""

    def test_known_key_without_placeholders_returns_text(self):
        """A key with no ``{placeholder}`` returns its text verbatim."""
        result = t("state.idle")
        assert result == "idle"

    def test_known_key_with_placeholder_substitutes_value(self):
        """A key with ``{name}`` substitutes the kwarg value."""
        # ``notify.app.undo_done`` = "Undid last transcription ({char_count} chars)"
        result = t("notify.app.undo_done", char_count=42)
        assert result == "Undid last transcription (42 chars)"

    def test_known_key_with_multiple_placeholders_substitutes_all(self):
        """Multiple ``{name}`` placeholders all substitute."""
        # ``notify.update_available_body`` =
        #     "{app} {version} is available (you have {current})"
        result = t(
            "notify.update_available_body",
            app="Voice Typer",
            version="2.0.0",
            current="1.5.0",
        )
        assert result == "Voice Typer 2.0.0 is available (you have 1.5.0)"

    def test_empty_fmt_does_not_attempt_format_call(self):
        """When ``fmt`` is empty, the format path is skipped entirely
        (the function short-circuits with ``if fmt:``). A key with a
        ``{placeholder}`` but no kwargs returns the raw text unchanged.
        """
        # ``notify.app.undo_done`` has ``{char_count}`` but we pass no kwargs.
        result = t("notify.app.undo_done")
        # No fmt → no format call → text returned as-is (with placeholder).
        assert result == "Undid last transcription ({char_count} chars)"


# ── (b) register_locale + set_locale + get_locale round-trip ────────────


class TestLocaleRegistry:
    """``register_locale`` + ``set_locale`` + ``get_locale`` round-trip."""

    def test_register_locale_then_set_locale_then_get_locale_round_trip(self):
        """Registering a new locale, switching to it, and querying it
        returns the registered locale code.
        """
        register_locale("xx", {"state.idle": "xx-idle"})
        set_locale("xx")
        assert get_locale() == "xx"

    def test_set_locale_to_unregistered_locale_falls_back_to_english(self):
        """``set_locale("never-registered")`` must fall back to ``"en"``
        rather than crash or leave the locale in a half-set state.
        """
        set_locale("never-registered")
        assert get_locale() == "en"

    def test_set_locale_back_to_english(self):
        """After switching away from English, switching back restores it."""
        register_locale("yy", {"state.idle": "yy-idle"})
        set_locale("yy")
        assert get_locale() == "yy"
        set_locale("en")
        assert get_locale() == "en"

    def test_register_locale_overwrites_previous_registration(self):
        """Re-registering the same locale replaces its label set
        (not merge — full replacement, mirroring the IPC semantics where
        the renderer pushes the full label dict on locale change).
        """
        register_locale("zz", {"state.idle": "first", "state.recording": "first-rec"})
        register_locale("zz", {"state.idle": "second"})
        set_locale("zz")
        # The second registration wins; "state.recording" is gone.
        assert t("state.idle") == "second"
        # Falls back to English for the dropped key.
        assert t("state.recording") == "recording"

    def test_register_locale_does_not_mutate_caller_dict(self):
        """The caller's labels dict is copied (not stored by reference)."""
        caller_labels = {"state.idle": "qq-idle"}
        register_locale("qq", caller_labels)
        # Mutate the caller's dict after registration.
        caller_labels["state.idle"] = "MUTATED"
        set_locale("qq")
        # The registered value must NOT reflect the mutation.
        assert t("state.idle") == "qq-idle"


# ── (c) Fallback chain: active → English → raw key ──────────────────────


class TestFallbackChain:
    """``t`` falls back to English when the active locale is missing a key,
    then to the raw key string when English also lacks it."""

    def test_missing_key_in_active_locale_present_in_english_returns_english_text(self):
        """Active locale lacks the key, English has it → English text."""
        register_locale("es", {"state.idle": "inactivo"})
        set_locale("es")
        # "state.recording" is missing from "es" but present in English.
        result = t("state.recording")
        assert result == "recording"

    def test_missing_key_in_active_locale_and_english_returns_raw_key(self):
        """Active locale and English both lack the key → raw key string."""
        register_locale("es", {"state.idle": "inactivo"})
        set_locale("es")
        result = t("totally.missing.key")
        assert result == "totally.missing.key"

    def test_missing_key_in_english_only_returns_raw_key(self):
        """When the active locale is English (default) and the key is
        absent, the raw key string is returned — loudly visible in the UI.
        """
        set_locale("en")
        result = t("nonexistent.key")
        assert result == "nonexistent.key"

    def test_english_fallback_still_applies_format_placeholders(self):
        """When the active locale lacks a key but English has it (with
        placeholders), the format substitution still applies on the
        English fallback text.
        """
        register_locale("es", {"state.idle": "inactivo"})
        set_locale("es")
        # "notify.app.undo_done" is missing from "es" → English fallback,
        # which has ``{char_count}`` placeholder.
        result = t("notify.app.undo_done", char_count=7)
        assert result == "Undid last transcription (7 chars)"


# ── (d) Format interpolation failure ────────────────────────────────────


class TestFormatInterpolationFailure:
    """Format interpolation failures (missing placeholder) return the
    unformatted text rather than raising — so a bad translation never
    crashes a notification path."""

    def test_missing_placeholder_returns_unformatted_text(self):
        """Key has ``{char_count}`` but caller passes no ``char_count``
        kwarg → ``KeyError`` is caught, unformatted text returned.
        """
        # ``notify.app.undo_done`` = "Undid last transcription ({char_count} chars)"
        # Passing a DIFFERENT placeholder so char_count is missing.
        result = t("notify.app.undo_done", unrelated="value")
        assert result == "Undid last transcription ({char_count} chars)"

    def test_no_kwargs_with_placeholder_key_returns_unformatted_text(self):
        """Key has ``{name}`` placeholder, no kwargs passed at all →
        ``KeyError`` is caught, unformatted text returned.

        (The ``if fmt:`` guard skips format when ``fmt`` is empty — but
        here we pass a dummy kwarg to force the format path.)
        """
        # Force the format path with a dummy kwarg while omitting the
        # required ``char_count``.
        result = t("notify.app.undo_done", _dummy="x")
        assert result == "Undid last transcription ({char_count} chars)"

    def test_correct_kwargs_with_no_placeholders_returns_text(self):
        """Sanity: extra kwargs on a key with no placeholders are ignored
        (Python's ``str.format`` silently ignores extra kwargs).
        """
        result = t("state.idle", unused_kwarg="ignored")
        assert result == "idle"

    def test_bad_format_spec_returns_unformatted_text(self):
        """AC-22 — a translation whose format spec is invalid (e.g.
        ``{name:bad}``) must NOT raise ``ValueError``. The previous
        ``except (KeyError, IndexError)`` only caught missing-
        placeholder / index errors, not bad-format-spec errors —
        ``str.format`` raises ``ValueError`` for an unknown format
        spec, which would crash a notification path. The catch is now
        broadened to ``(KeyError, IndexError, ValueError)``.
        """
        register_locale("en", {"test.bad_format_spec": "Hello {name:bad}"})
        result = t("test.bad_format_spec", name="world")
        # The bad format spec is caught; unformatted text returned.
        assert result == "Hello {name:bad}"


# ── (e) Thread-safety ───────────────────────────────────────────────────


class TestThreadSafety:
    """Concurrent ``register_locale`` + ``t`` calls must not raise or
    corrupt the registry. The module guards all reads/writes with a
    module-level ``threading.Lock`` (``i18n._LOCK``).
    """

    def test_concurrent_register_and_translate_no_exceptions(self):
        """8 threads racing ``register_locale`` + ``t`` for 200 iterations
        each must complete without raising. The lock prevents the
        ``dict.get`` on a partially-mutated ``_REGISTRY`` from seeing
        inconsistent state.
        """
        errors: list[BaseException] = []

        def worker(thread_id: int) -> None:
            try:
                for i in range(200):
                    locale_name = f"thread-{thread_id}-{i % 4}"
                    register_locale(locale_name, {"state.idle": f"idle-{thread_id}-{i}"})
                    # Switch locale every other iteration so reads race
                    # with the registry writes above.
                    if i % 2 == 0:
                        set_locale(locale_name)
                    else:
                        set_locale("en")
                    # Translate while another thread may be mid-write.
                    _ = t("state.idle")
                    _ = t("notify.app.undo_done", char_count=i)
                    _ = t("totally.missing.key")  # raw-key fallback path
            except BaseException as exc:  # noqa: BLE001 — collected for assertion
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(tid,)) for tid in range(8)]
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=10.0)

        assert not errors, f"concurrent register/t raised: {errors}"
        # After the race, the module must still be in a consistent state —
        # ``get_locale`` returns a string, ``t`` returns a string.
        assert isinstance(get_locale(), str)
        assert isinstance(t("state.idle"), str)

    def test_concurrent_set_locale_does_not_leave_locale_in_half_set_state(self):
        """4 threads racing ``set_locale`` must converge on a registered
        locale — ``get_locale`` never returns an unregistered code.
        """
        register_locale("alpha", {"state.idle": "alpha"})
        register_locale("beta", {"state.idle": "beta"})
        register_locale("gamma", {"state.idle": "gamma"})

        stop = threading.Event()
        errors: list[BaseException] = []

        def switcher() -> None:
            try:
                while not stop.is_set():
                    for loc in ("alpha", "beta", "gamma", "en"):
                        set_locale(loc)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=switcher) for _ in range(4)]
        for th in threads:
            th.start()
        try:
            # Spin for ~50ms of concurrent set_locale calls; sample get_locale.
            for _ in range(500):
                loc = get_locale()
                assert loc in {"alpha", "beta", "gamma", "en"}, f"get_locale returned unregistered code: {loc!r}"
        finally:
            stop.set()
        for th in threads:
            th.join(timeout=5.0)

        assert not errors, f"concurrent set_locale raised: {errors}"
