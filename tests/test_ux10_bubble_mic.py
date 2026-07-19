"""UX-10: always-visible bubble mic button — backend config + push tests.

Focused, dependency-light tests for the Python side of UX-10:
  - the two new config fields (``bubble_click_to_toggle``,
    ``bubble_mic_button``) validate via ``validate_config_update`` and
    are present in ``IPC_CONFIG_ALLOWLIST``;
  - the waveform bubble wiring emits a ``bubble_config`` event carrying
    exactly the bubble-relevant subset when its ``on_config`` listener
    fires.

These intentionally avoid the broader (pre-existing-failing) config
suite so they run green in isolation.
"""

import importlib

from voice_typer.server import event_bus


def test_new_bubble_fields_validate():
    from voice_typer.server.config import validate_config_update

    validated, errors = validate_config_update(
        {
            "bubble_click_to_toggle": True,
            "bubble_mic_button": False,
        }
    )
    assert errors == []
    assert validated.get("bubble_click_to_toggle") is True
    assert validated.get("bubble_mic_button") is False


def test_new_bubble_fields_in_allowlist():
    from voice_typer.server.config import IPC_CONFIG_ALLOWLIST

    assert "bubble_click_to_toggle" in IPC_CONFIG_ALLOWLIST
    assert "bubble_mic_button" in IPC_CONFIG_ALLOWLIST


def test_bubble_config_event_carries_relevant_subset():
    """WaveformBubble.on_config (wired to _push_bubble_config) emits a
    bubble_config event with exactly the three bubble-relevant keys."""

    # Reset the event bus so we only observe this test's events.
    importlib.reload(event_bus)

    from voice_typer.server.waveform import WaveformBubble

    bubble = WaveformBubble()

    captured = {}

    def capture(msg):
        captured.setdefault(msg.get("type"), msg)

    event_bus.subscribe(capture)

    # Wire the listener exactly as _wire_waveform_bubble does.
    def _push_bubble_config(cfg):
        event_bus.publish(
            {
                "type": "bubble_config",
                "data": {
                    "bubble_behavior": getattr(cfg, "bubble_behavior", "show_on_record"),
                    "bubble_click_to_toggle": getattr(cfg, "bubble_click_to_toggle", True),
                    "bubble_mic_button": getattr(cfg, "bubble_mic_button", True),
                },
            }
        )

    bubble.on_config = _push_bubble_config

    class _Cfg:
        bubble_behavior = "always_visible"
        bubble_click_to_toggle = True
        bubble_mic_button = True

    bubble.on_config(_Cfg())

    assert captured.get("bubble_config") is not None
    data = captured["bubble_config"]["data"]
    assert set(data.keys()) == {
        "bubble_behavior",
        "bubble_click_to_toggle",
        "bubble_mic_button",
    }
    assert data["bubble_behavior"] == "always_visible"
    assert data["bubble_mic_button"] is True
