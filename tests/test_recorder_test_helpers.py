"""Parity tests for ``tests/fixtures/recorder_test_helpers.py``.

``WORKER_THREAD_NAMES`` is the shared frozenset used by the recorder
worker-lifecycle guard helpers (``reap_stale_worker_refs`` /
``wait_for_workers_stopped``) to detect live worker threads by name.

The fixture deliberately keeps it as a literal (see its module
docstring — importing the server constants there would drag in the
heavy recording package for every test that only needs the guard).
This parity test pins the literal against the REAL source of truth in
``voice_typer.server.recording.recorder`` so a rename in the server
code can never silently drift the guard.

The two spawn-site names (``stream-finished-handler`` in
``recorder.py``, ``device-disconnect-handler`` in
``audio_pipeline.py``) are plain string literals at their spawn sites
— they have no constants to import — so they are pinned here
alongside the two constant-backed names.
"""

from __future__ import annotations

from voice_typer.server.recording.recorder import (
    _AUDIO_WORKER_THREAD_NAME,
    _EVENT_WORKER_THREAD_NAME,
)

from tests.fixtures.recorder_test_helpers import WORKER_THREAD_NAMES


def test_worker_thread_names_match_recorder_constants() -> None:
    """The guard's worker-name set must match the real worker threads.

    Two names come from the exported ``_AUDIO_WORKER_THREAD_NAME`` /
    ``_EVENT_WORKER_THREAD_NAME`` constants; the other two are the
    thread names used at the spawn sites in ``recorder.py``
    (``_stream_finished_callback``) and ``audio_pipeline.py``
    (``_handle_device_disconnect``).
    """
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
