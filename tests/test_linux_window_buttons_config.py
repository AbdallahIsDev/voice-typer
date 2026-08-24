"""End-to-end IPC tests for the ``linux_window_buttons`` config field.

Pins the SEC-002 surface for the new key: it must be settable through
``validate_config_update`` (Settings → Appearance → Window Buttons) with
the full 5-key shape, and malformed shapes must be rejected.
"""

from __future__ import annotations

from voice_typer.server.config import Config
from voice_typer.server.config_validators import validate_config_update

_VALID = {
    "mode": "custom",
    "side": "left",
    "show_minimize": True,
    "show_maximize": False,
    "show_close": True,
}


def test_valid_shape_passes_validation():
    cleaned, errors = validate_config_update({"linux_window_buttons": dict(_VALID)})
    assert errors == []
    assert cleaned["linux_window_buttons"] == _VALID


def test_dataclass_default_is_valid():
    default = Config().linux_window_buttons
    cleaned, errors = validate_config_update({"linux_window_buttons": default})
    assert errors == []
    assert cleaned["linux_window_buttons"] == default


def test_invalid_mode_is_dropped():
    bad = dict(_VALID, mode="sometimes")
    cleaned, errors = validate_config_update({"linux_window_buttons": bad})
    assert "linux_window_buttons" not in cleaned
    assert errors and "mode" in errors[0]


def test_invalid_side_is_dropped():
    bad = dict(_VALID, side="middle")
    cleaned, errors = validate_config_update({"linux_window_buttons": bad})
    assert "linux_window_buttons" not in cleaned
    assert errors


def test_non_bool_flag_is_dropped():
    bad = dict(_VALID, show_close="yes")
    cleaned, errors = validate_config_update({"linux_window_buttons": bad})
    assert "linux_window_buttons" not in cleaned
    assert errors


def test_unknown_key_is_dropped():
    bad = dict(_VALID, color="red")
    cleaned, errors = validate_config_update({"linux_window_buttons": bad})
    assert "linux_window_buttons" not in cleaned
    assert errors


def test_non_dict_is_dropped():
    cleaned, errors = validate_config_update({"linux_window_buttons": "left"})
    assert "linux_window_buttons" not in cleaned
    assert errors
