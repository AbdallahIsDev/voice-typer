"""Shared ``Recorder`` factory for the secure-clear / hot-swap test suite.

This module owns the SINGLE canonical ``_make_recorder`` helper used by
every test file that exercises the secure-clear / hot-swap path on
:class:`voice_typer.server.recording.recorder.Recorder`.

Before this module existed, two byte-for-byte copies of the helper
were sprinkled across the test tree:

- ``tests/test_secure_clear_no_resample_segments.py`` (line 46)
- ``tests/test_secure_clear_array.py`` (line 131)

Both copies construct a real ``Recorder`` with a ``MagicMock`` config
(sensible values for the fields the secure-clear path touches) and
patch ``voice_typer.server.vad.is_available`` to return ``False`` so
the test doesn't pay the ~17s torch-import cost on the sandbox.

The two copies were literally identical (verified by ``diff``) — a
classic copy-paste leak that drifts the next time one is updated and
the other isn't.

Centralising the factory here means future additions to the
``Recorder`` constructor (e.g. a new ``_secure_clear`` field that
tests need to reset between cases) only need to update ONE place —
this module — and every secure-clear test picks up the fix
automatically.

The migration target is documented as Remaining Work:
this module ONLY provides the factory — call sites
(``tests/test_secure_clear_no_resample_segments.py`` and
``tests/test_secure_clear_array.py``) still use their inline
``_make_recorder`` until a follow-up task migrates them.

NOTE: there are OTHER ``_make_recorder`` helpers in the test tree
(e.g. ``tests/test_recorder_mono_and_disconnect_fixes.py``,
``tests/test_hot_swap_secure_clear.py``,
``tests/test_recording_discard.py``,
``tests/test_recorder_device_cache_prewarm.py``,
``tests/test_audio_pipeline_process_chunk.py``,
``tests/test_stream_lifecycle_module.py``). Those have DIFFERENT
shapes (different config field sets, different post-construction
mutations like ``r._recording_event.set()`` / ``r._stream = MagicMock()``)
and are NOT byte-for-byte duplicates — they are documented as
Remaining Work for a separate consolidation pass. This module targets
ONLY the two byte-for-byte identical copies that were explicitly
called out for consolidation.
"""

from __future__ import annotations

from typing import Any


def make_recorder() -> Any:
    """Build a minimal ``Recorder`` with the fields the secure-clear path touches.

    Avoids spawning real audio threads / sounddevice probes. The VAD
    availability check is mocked out because importing torch takes
    ~17s on this sandbox (see ``voice_typer.server.vad.is_available``),
    which blows the per-test timeout. The secure-clear path doesn't
    depend on VAD, so mocking the check is safe.

    The returned ``Recorder`` is configured with a ``MagicMock`` config
    that pre-populates the fields the secure-clear path reads:

    - ``config.sample_rate = 16000``
    - ``config.microphone = None``
    - ``config.silence_warning_seconds = 20.0``
    - ``config.stop_on_silence_seconds = 120.0``
    - ``config.max_recording_time_seconds = 900``
    - ``config.device = "cpu"``

    Tests that need different values (e.g. ``config.sample_rate = 48000``
    to exercise the resample path) should override the field on the
    returned instance — the constructor has already run, but the
    secure-clear path reads ``config.sample_rate`` lazily so a
    post-construction mutation is honoured.

    Returns
    -------
    voice_typer.server.recording.recorder.Recorder
        A real ``Recorder`` instance with a mocked config. Caller is
        free to mutate any attribute before exercising the secure-clear
        path.
    """
    from unittest.mock import MagicMock, patch

    from voice_typer.server.recording import Recorder

    config = MagicMock()
    config.sample_rate = 16000
    config.microphone = None
    config.silence_warning_seconds = 20.0
    config.stop_on_silence_seconds = 120.0
    config.max_recording_time_seconds = 900
    config.device = "cpu"
    with patch("voice_typer.server.vad.is_available", return_value=False):
        return Recorder(config)


__all__ = [
    "make_recorder",
]
