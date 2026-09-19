"""Parity tests for ``tests/fixtures/recorder_test_helpers.py``."""

from __future__ import annotations

from voice_typer.server.recording.recorder import (
    _AUDIO_WORKER_THREAD_NAME,
    _EVENT_WORKER_THREAD_NAME,
)

from tests.fixtures.recorder_test_helpers import WORKER_THREAD_NAMES


def test_worker_thread_names_match_recorder_constants() -> None:
    """The guard's worker-name set must match the real worker threads."""
    expected = frozenset(
        {
            _AUDIO_WORKER_THREAD_NAME,
            _EVENT_WORKER_THREAD_NAME,
            "stream-finished-handler",
            "device-disconnect-handler",
        }
    )
    assert expected == WORKER_THREAD_NAMES, (
        "WORKER_THREAD_NAMES drifted from the real recorder worker "
        f"thread names: expected {sorted(expected)}, got "
        f"{sorted(WORKER_THREAD_NAMES)}. If a worker thread was renamed "
        "or added in voice_typer/server/recording (or its spawn-site "
        "literal changed), update the fixture's WORKER_THREAD_NAMES "
        "literal to match."
    )
