"""Tests for ``voice_typer.server.duration.format_duration`` (C-LOG-2)."""

from __future__ import annotations

import pytest
from voice_typer.server.duration import format_duration


def test_sub_second() -> None:
    assert format_duration(0.123) == " 0.1s"


def test_typical_seconds() -> None:
    assert format_duration(2.3) == " 2.3s"


def test_exactly_one_second() -> None:
    assert format_duration(1.0) == " 1.0s"


def test_just_under_a_minute() -> None:
    assert format_duration(59.4) == " 59.4s"


def test_over_a_minute_uses_minutes() -> None:
    assert format_duration(62.3) == " 1m 2.3s"


def test_multiple_minutes() -> None:
    assert format_duration(185.0) == " 3m 5.0s"


def test_negative_duration_clamped_to_zero() -> None:
    assert format_duration(-3.0) == " 0.0s"


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0.0, " 0.0s"),
        (0.5, " 0.5s"),
        (7.0, " 7.0s"),
        (59.9, " 59.9s"),
        (59.96, " 1m 0.0s"),
        (60.0, " 1m 0.0s"),
        (119.9, " 1m 59.9s"),
        (120.0, " 2m 0.0s"),
        (3600.0, " 60m 0.0s"),
    ],
)
def test_format_duration_table(seconds: float, expected: str) -> None:
    assert format_duration(seconds) == expected
