"""YJ-24 regression test: non-string hotkey values are coerced to ``None``.

YJ-FIX-B tightened the ``hotkey_values[name] = getattr(cfg, name)`` line
in :func:`voice_typer.server.config_validators.validate_config` to::

    raw = getattr(cfg, name)
    hotkey_values[name] = raw if isinstance(raw, str) else None

so the ``hotkey_values`` dict (typed ``dict[str, str | None]``) actually
matches its annotation at runtime. Pre-YJ-24, a hand-edited
``config.json`` that held a non-string hotkey value (e.g. ``123`` or
``true``) was stored AS-IS in ``hotkey_values``, violating the type
contract. The YJ-FIX-B change is a documented behaviour change (the
non-string value is now coerced to ``None`` before being passed to
:func:`_check_cross_field_hotkey_conflicts`).

This test pins the new coercion behaviour so a future refactor cannot
silently revert it. The test is white-box: it mocks
:func:`_check_cross_field_hotkey_conflicts` to capture the
``field_values`` argument it received and asserts the non-string value
was coerced to ``None``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from voice_typer.server.config_validators import (
    _check_cross_field_hotkey_conflicts,
    validate_config,
)


class TestNonStringHotkeyCoercion:
    """Pin the YJ-24 behaviour: non-string hotkeys become ``None``.

    Each test constructs a ``Config``-like object with one or more
    hotkey fields set to a non-string value, calls
    :func:`validate_config`, and asserts:

    * the function does NOT raise;
    * the non-string value was coerced to ``None`` before reaching
      :func:`_check_cross_field_hotkey_conflicts` (captured via a
      ``patch`` on the helper).
    """

    def test_int_hotkey_coerced_to_none(self) -> None:
        """``hotkey = 123`` (int from a hand-edited config.json) is
        coerced to ``None`` before the cross-field check runs.
        """
        cfg = SimpleNamespace(
            hotkey=123,  # type: ignore[assignment]
            repaste_hotkey="<f6>",
        )
        captured: dict = {}

        def _capture(field_values):
            captured["field_values"] = field_values
            return []

        with patch(
            "voice_typer.server.config_validators._check_cross_field_hotkey_conflicts",
            side_effect=_capture,
        ):
            errors = validate_config(cfg)

        # function does NOT raise.
        assert isinstance(errors, list)
        # the non-string ``123`` was coerced to ``None`` (NOT
        # stored as-is). The pre- behaviour would have left
        # ``hotkey=123`` in the dict.
        assert "field_values" in captured, "_check_cross_field_hotkey_conflicts was not invoked — patch failed"
        fv = captured["field_values"]
        assert fv["hotkey"] is None, f"expected hotkey coerced to None, got {fv['hotkey']!r}"
        # The legitimate string hotkey passes through unchanged.
        assert fv["repaste_hotkey"] == "<f6>"

    def test_bool_hotkey_coerced_to_none(self) -> None:
        """``hotkey = True`` (bool from a hand-edited config.json) is
        also coerced to ``None`` (bool is NOT str).
        """
        cfg = SimpleNamespace(
            hotkey=True,  # type: ignore[assignment]
            repaste_hotkey="<f6>",
        )
        captured: dict = {}

        def _capture(field_values):
            captured["field_values"] = field_values
            return []

        with patch(
            "voice_typer.server.config_validators._check_cross_field_hotkey_conflicts",
            side_effect=_capture,
        ):
            errors = validate_config(cfg)

        assert isinstance(errors, list)
        fv = captured["field_values"]
        assert fv["hotkey"] is None, f"expected bool hotkey coerced to None, got {fv['hotkey']!r}"

    def test_list_hotkey_coerced_to_none(self) -> None:
        """A list value (e.g. from a botched migration that wrote a
        multi-character array) is also coerced to ``None``.
        """
        cfg = SimpleNamespace(
            hotkey=["<ctrl>", "<space>"],  # type: ignore[assignment]
            repaste_hotkey="<f6>",
        )
        captured: dict = {}

        def _capture(field_values):
            captured["field_values"] = field_values
            return []

        with patch(
            "voice_typer.server.config_validators._check_cross_field_hotkey_conflicts",
            side_effect=_capture,
        ):
            errors = validate_config(cfg)

        assert isinstance(errors, list)
        fv = captured["field_values"]
        assert fv["hotkey"] is None, f"expected list hotkey coerced to None, got {fv['hotkey']!r}"

    def test_all_hotkeys_non_string_does_not_raise(self) -> None:
        """If ALL hotkey fields are non-string (e.g. a corrupted
        config where every hotkey is an int), :func:`validate_config`
        must NOT raise and must pass ``None`` for each to the
        cross-field helper.
        """
        cfg = SimpleNamespace(
            hotkey=123,  # type: ignore[assignment]
            repaste_hotkey=456,  # type: ignore[assignment]
        )
        captured: dict = {}

        def _capture(field_values):
            captured["field_values"] = field_values
            return []

        with patch(
            "voice_typer.server.config_validators._check_cross_field_hotkey_conflicts",
            side_effect=_capture,
        ):
            errors = validate_config(cfg)

        assert isinstance(errors, list)
        fv = captured["field_values"]
        assert fv == {
            "hotkey": None,
            "repaste_hotkey": None,
        }, f"expected all-None hotkey_values, got {fv!r}"

    def test_string_hotkey_not_coerced(self) -> None:
        """Sanity check: a valid string hotkey is NOT coerced — the
        coercion only applies to non-string values. This guards against
        a future regression that over-eagerly coerces everything to
        ``None``.
        """
        cfg = SimpleNamespace(
            hotkey="<f5>",
            repaste_hotkey="<f6>",
        )
        captured: dict = {}

        def _capture(field_values):
            captured["field_values"] = field_values
            return []

        with patch(
            "voice_typer.server.config_validators._check_cross_field_hotkey_conflicts",
            side_effect=_capture,
        ):
            errors = validate_config(cfg)

        assert isinstance(errors, list)
        fv = captured["field_values"]
        assert fv["hotkey"] == "<f5>", f"string hotkey should pass through unchanged, got {fv['hotkey']!r}"

    def test_mixed_string_and_non_string_hotkeys(self) -> None:
        """Mix of valid string hotkeys and non-string hotkeys in the
        same config: only the non-string ones are coerced.
        """
        cfg = SimpleNamespace(
            hotkey="<f5>",  # valid string
            repaste_hotkey=0,  # type: ignore[assignment]  # non-string (int 0)
        )
        captured: dict = {}

        def _capture(field_values):
            captured["field_values"] = field_values
            return []

        with patch(
            "voice_typer.server.config_validators._check_cross_field_hotkey_conflicts",
            side_effect=_capture,
        ):
            errors = validate_config(cfg)

        assert isinstance(errors, list)
        fv = captured["field_values"]
        assert fv["hotkey"] == "<f5>", "string hotkey should pass through"
        assert fv["repaste_hotkey"] is None, f"int 0 hotkey should be coerced to None, got {fv['repaste_hotkey']!r}"

    def test_validate_config_returns_list_without_raising(self) -> None:
        """End-to-end: ``validate_config`` on a config with non-string
        hotkeys returns a list (possibly empty) without raising.
        """
        cfg = SimpleNamespace(
            hotkey=123,  # type: ignore[assignment]
            repaste_hotkey=456,  # type: ignore[assignment]
        )
        # No patch — exercise the real cross-field check too. The
        # non-string values are coerced to None, so the real
        # ``_check_cross_field_hotkey_conflicts`` will skip them
        # (``isinstance(None, str)`` is False) and return [].
        errors = validate_config(cfg)
        assert isinstance(errors, list)
        # No conflict because the coerced None values are all skipped.
        assert not any("Hotkey conflict" in e for e in errors), f"unexpected hotkey conflict from None values: {errors}"

    def test_check_cross_field_helper_handles_none_directly(self) -> None:
        """Direct call to the real (un-patched)
        :func:`_check_cross_field_hotkey_conflicts` with the post-YJ-24
        shape (all-None values) returns an empty error list.
        """
        errors = _check_cross_field_hotkey_conflicts(
            {
                "hotkey": None,
                "repaste_hotkey": None,
            }
        )
        assert errors == [], f"expected no conflicts for all-None hotkeys, got {errors}"
