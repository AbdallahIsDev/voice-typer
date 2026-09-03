"""``text_size`` propagation into the bubble config push.

The bubble renderer's ``useThemeSync`` hook scales the pill's text with
the user's UI text-size setting. That setting reaches the sandboxed
bubble renderer ONLY via the ``bubble_config`` push event, so the push
payload must carry ``text_size`` (with the same truthiness fallback the
enum keys use — a missing/null value falls back to the config default
14), and ``set_config({text_size: ...})`` must trigger a fresh push.
"""

from __future__ import annotations

from types import SimpleNamespace

from tests.test_tray import _CapturingWiring


class TestPushBubbleConfigCarriesTextSize:
    """``_push_bubble_config`` payload includes ``text_size``."""

    def test_real_value_is_forwarded(self):
        wiring = _CapturingWiring()
        try:
            event = wiring.push_config(SimpleNamespace(text_size=18, custom_theme=None))
            assert event is not None
            assert event["data"]["text_size"] == 18
        finally:
            wiring.stop()

    def test_missing_attribute_falls_back_to_default_14(self):
        """A cfg without the attribute (minimal mock) carries 14."""
        wiring = _CapturingWiring()
        try:
            event = wiring.push_config(SimpleNamespace())
            assert event is not None
            assert event["data"]["text_size"] == 14
        finally:
            wiring.stop()

    def test_none_value_falls_back_to_default_14(self):
        """``None`` text_size (partial/corrupt config load) → 14."""
        wiring = _CapturingWiring()
        try:
            event = wiring.push_config(SimpleNamespace(text_size=None, custom_theme=None))
            assert event is not None
            assert event["data"]["text_size"] == 14
        finally:
            wiring.stop()

    def test_text_size_change_triggers_bubble_config_repush(self):
        """The set_config handler's trigger list includes ``text_size``:
        changing the UI text size must publish a fresh ``bubble_config``
        so the bubble's font scale follows live (not only after restart).

        The full set_config flow (validation, persistence, ack) is
        covered by ``tests/handlers/test_config_handlers.py``; this pins
        the trigger tuple itself, which is the piece the live font-scale
        feature depends on.
        """
        import inspect

        from voice_typer.server.handlers import config_handlers

        src = inspect.getsource(config_handlers)
        start = src.index("if any(")
        block = src[start : src.index("):", start)]
        assert '"text_size"' in block, (
            "set_config bubble_config trigger list must include text_size "
            "so the bubble receives font-scale updates live"
        )
