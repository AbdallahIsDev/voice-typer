"""Bubble drag-position persistence — server-side contract tests.

Covers the three Python-side pieces of the durable bubble-position
feature:

1. **Allowlist bounds** — ``bubble_x`` / ``bubble_y`` must accept the
   negative coordinates produced by multi-monitor layouts (displays left
   of / above the primary have negative origins) while still rejecting
   absurd values.
2. **``bubble_config`` transport** — ``_push_bubble_config`` must forward
   the persisted pair VERBATIM to hosts. A coordinate of ``0`` is valid,
   so the truthiness fallback used for the enum/bool keys would be a
   silent-corruption bug here; these tests pin the plain-``getattr``
   semantics.
3. **Edge-toggle reset** — ``set_config({bubble_position: ...})`` clears
   the durable pair (both coordinates back to ``None``) and triggers the
   ``bubble_config`` repush so BOTH runtimes drop their cached position
   in-session. An explicit pair in the SAME payload wins.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from voice_typer.server.config_validators import validate_config_update

# ── 1. Allowlist bounds ────────────────────────────────────────────────


class TestBubbleCoordinateBounds:
    """``bubble_x`` / ``bubble_y`` accept multi-monitor negative coords."""

    @pytest.mark.parametrize("x,y", [(-1920, 0), (0, -1440), (-100_000, -100_000)])
    def test_negative_and_zero_coordinates_accepted(self, x: int, y: int):
        validated, errors = validate_config_update({"bubble_x": x, "bubble_y": y})
        assert errors == []
        assert validated == {"bubble_x": x, "bubble_y": y}

    def test_positive_coordinates_still_accepted(self):
        validated, errors = validate_config_update({"bubble_x": 3840, "bubble_y": 2160})
        assert errors == []
        assert validated == {"bubble_x": 3840, "bubble_y": 2160}

    @pytest.mark.parametrize("coord", [100_001, -100_001])
    def test_out_of_range_coordinate_rejected_atomically(self, coord: int):
        validated, errors = validate_config_update({"bubble_x": coord})
        assert errors, f"coordinate {coord} must be rejected"
        assert validated == {}

    def test_none_sentinel_still_accepted(self):
        """``None`` remains the documented 'never dragged' value."""
        validated, errors = validate_config_update({"bubble_x": None, "bubble_y": None})
        assert errors == []
        assert validated == {"bubble_x": None, "bubble_y": None}


# ── 2. bubble_config transport ────────────────────────────────────────


class TestPushBubbleConfigCarriesPosition:
    """``_push_bubble_config`` forwards the persisted pair verbatim."""

    @pytest.fixture
    def wiring(self):
        # Reuse the existing capture harness (P2 — import the source,
        # don't copy it).
        from tests.test_tray import _CapturingWiring

        w = _CapturingWiring()
        yield w
        w.stop()

    @staticmethod
    def _cfg(x: int | None, y: int | None) -> Any:
        class _Cfg:
            bubble_behavior = "show_on_record"
            bubble_click_to_toggle = True
            bubble_mic_button = True
            theme_mode = "system"
            theme_preset = "default"
            custom_theme = None

        cfg = _Cfg()
        cfg.bubble_x = x
        cfg.bubble_y = y
        return cfg

    def test_payload_carries_dragged_pair_verbatim(self, wiring):
        event = wiring.push_config(self._cfg(-1920, 1040))
        assert event is not None
        assert event["data"]["bubble_x"] == -1920
        assert event["data"]["bubble_y"] == 1040

    def test_zero_coordinate_is_not_discarded_by_truthiness_fallback(self, wiring):
        """``0`` is falsy — an ``or default`` fallback would corrupt it."""
        event = wiring.push_config(self._cfg(0, 0))
        assert event is not None
        assert event["data"]["bubble_x"] == 0
        assert event["data"]["bubble_y"] == 0

    def test_unset_position_pushes_nulls(self, wiring):
        event = wiring.push_config(self._cfg(None, None))
        assert event is not None
        assert event["data"]["bubble_x"] is None
        assert event["data"]["bubble_y"] is None

    def test_missing_attributes_push_nulls(self, wiring):
        """A cfg without the attrs entirely (minimal mock) pushes None."""

        class _BareCfg:
            bubble_behavior = "show_on_record"
            bubble_click_to_toggle = True
            bubble_mic_button = True
            theme_mode = "system"
            theme_preset = "default"
            custom_theme = None

        event = wiring.push_config(_BareCfg())
        assert event is not None
        assert event["data"]["bubble_x"] is None
        assert event["data"]["bubble_y"] is None


# ── 3. Edge-toggle reset ───────────────────────────────────────────────


class TestEdgeToggleClearsDurablePosition:
    """``set_config({bubble_position})`` resets the persisted pair."""

    def test_toggle_clears_pair_and_repushes(self, ipc_server: Any, fake_app: MagicMock, fake_service: MagicMock):
        resp = ipc_server._handle_set_config({"bubble_position": "top"}, {})

        assert resp["type"] == "ack"
        applied = fake_service.apply_config.call_args[0][0]
        assert applied["bubble_position"] == "top"
        assert applied["bubble_x"] is None
        assert applied["bubble_y"] is None
        # The repush must fire so both runtimes drop their cached pair
        # in-session (not just after a restart).
        fake_app.push_bubble_config.assert_called_once()

    def test_explicit_pair_in_same_payload_wins_over_reset(self, ipc_server: Any, fake_service: MagicMock):
        resp = ipc_server._handle_set_config({"bubble_position": "bottom", "bubble_x": 120, "bubble_y": 240}, {})

        assert resp["type"] == "ack"
        applied = fake_service.apply_config.call_args[0][0]
        assert applied["bubble_x"] == 120
        assert applied["bubble_y"] == 240

    def test_unrelated_keys_do_not_clear_or_repush(self, ipc_server: Any, fake_app: MagicMock, fake_service: MagicMock):
        resp = ipc_server._handle_set_config({"hotkey": "<f3>"}, {})

        assert resp["type"] == "ack"
        applied = fake_service.apply_config.call_args[0][0]
        assert "bubble_x" not in applied
        assert "bubble_y" not in applied
        fake_app.push_bubble_config.assert_not_called()
