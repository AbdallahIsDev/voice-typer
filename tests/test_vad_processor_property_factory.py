"""Tests for the ``_make_vad_property`` factory on :class:`VadProcessor`.

These tests pin the behavior of the module-level factory that replaced
18 hand-written @property/@setter pass-through pairs. They verify that:

* The factory produces a read/write ``property`` that delegates to the
  ``self._<attr>`` backing attribute (the same convention the original
  hand-written pairs used).
* Representative properties across the three categories — state enum
  (``state``), int counter (``consecutive_speech_frames``), bool flag
  (``calibrated``) — round-trip correctly.
* The two clamping properties (``speech_threshold_db`` /
  ``silence_threshold_db``) keep their hand-written R18-F14 floor logic
  and were NOT silently converted to plain pass-throughs.
* The public attribute surface is unchanged — the same property names
  exist on ``VadProcessor`` as before the refactor.

Follows the sibling-test-file convention already used elsewhere under
``voice_typer/server/`` (e.g. ``service/microphone_test.py``,
``handlers/cloud_test_handlers.py``).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from voice_typer.server.vad_processor import (
    MIN_VAD_SILENCE_THRESHOLD_DB,
    MIN_VAD_SPEECH_THRESHOLD_DB,
    VadProcessor,
    VadState,
    _make_vad_property,
)


def _config_with_vad_enabled() -> MagicMock:
    """Return a MagicMock config with at least one noise filter on.

    Mirrors the helper in ``tests/test_vad_processor.py`` so the
    ``VadProcessor`` constructor doesn't short-circuit on the VAD-GATE.
    """
    cfg = MagicMock()
    cfg.use_silero_vad = False  # force RMS path (no torch in test env)
    cfg.vad_speech_threshold = 0.5
    cfg.vad_silence_threshold = 0.3
    cfg.noise_filter_highpass = True
    cfg.noise_filter_gate = False
    cfg.noise_filter_eq = False
    cfg.noise_filter_compressor = False
    cfg.noise_filter_limiter = False
    cfg.noise_filter_notch = False
    cfg.noise_suppression_method = "none"
    return cfg


@pytest.fixture
def vp() -> VadProcessor:
    """Fresh ``VadProcessor`` for each test (no shared mutable state)."""
    return VadProcessor(_config_with_vad_enabled())


# ── Factory mechanics ──────────────────────────────────────────────────


def test_factory_returns_property_object() -> None:
    """The factory must return a ``property`` instance with both fget/fset."""
    prop = _make_vad_property("example_attr")
    assert isinstance(prop, property)
    assert prop.fget is not None
    assert prop.fset is not None


def test_factory_property_round_trips_to_backing_attr(vp: VadProcessor) -> None:
    """Setting via the factory property must write ``self._<attr>`` and
    reading must return it (the original hand-written pairs' contract)."""
    prop = _make_vad_property("hangover_frames")
    # Attach to a throwaway namespace to exercise the factory directly.
    type(vp)._test_hangover = prop
    try:
        # Initialize the backing attribute (the factory does NOT create it).
        vp._hangover_frames = 0
        assert vp._test_hangover == 0
        vp._test_hangover = 42
        assert vp._test_hangover == 42
        assert vp._hangover_frames == 42  # backing attr updated
    finally:
        delattr(type(vp), "_test_hangover")


# ── Representative property: state (enum) ──────────────────────────────


def test_state_factory_property_round_trip(vp: VadProcessor) -> None:
    """``state`` is a factory-generated property; verify get/set."""
    assert vp.state == VadState.UNKNOWN  # init value
    vp.state = VadState.SPEECH
    assert vp.state == VadState.SPEECH
    assert vp._state == VadState.SPEECH  # backing attr
    vp.state = VadState.SILENCE
    assert vp.state == VadState.SILENCE


def test_state_is_property_not_plain_attr(vp: VadProcessor) -> None:
    """``state`` must remain a descriptor (property), not a plain class
    attribute — the Recorder delegation shims rely on the property
    protocol."""
    assert isinstance(type(vp).state, property)
    assert isinstance(VadProcessor.state, property)


# ── Representative property: consecutive_speech_frames (int) ───────────


def test_consecutive_speech_frames_factory_round_trip(vp: VadProcessor) -> None:
    """``consecutive_speech_frames`` is factory-generated; verify get/set."""
    assert vp.consecutive_speech_frames == 0  # init value
    vp.consecutive_speech_frames = 3
    assert vp.consecutive_speech_frames == 3
    assert vp._consecutive_speech_frames == 3  # backing attr
    vp.consecutive_speech_frames = 0
    assert vp.consecutive_speech_frames == 0


# ── Representative property: calibrated (bool) ─────────────────────────


def test_calibrated_factory_round_trip(vp: VadProcessor) -> None:
    """``calibrated`` is factory-generated; verify get/set of bool."""
    assert vp.calibrated is False  # init value
    vp.calibrated = True
    assert vp.calibrated is True
    assert vp._calibrated is True  # backing attr
    vp.calibrated = False
    assert vp.calibrated is False


# ── All 18 factory properties exist on the public surface ──────────────


FACTORY_PROPERTY_NAMES = (
    "state",
    "consecutive_speech_frames",
    "consecutive_silence_frames",
    "speech_frames",
    "silence_frames",
    "hangover_frames",
    "use_silero_vad",
    "speech_threshold",
    "silence_threshold",
    "silero_available",
    "calibration_duration",
    "calibration_rms_values",
    "calibration_prob_values",
    "vad_auto_calibrate",
    "calibrated",
    "calibration_status",
    "vad_enabled_cached",
    "vad_enabled_cache_ts",
)


@pytest.mark.parametrize("name", FACTORY_PROPERTY_NAMES)
def test_factory_property_exists_and_is_readwrite(vp: VadProcessor, name: str) -> None:
    """Every name converted to the factory must still be a read/write
    property on the public surface — behavior preservation."""
    cls_attr = getattr(type(vp), name)
    assert isinstance(cls_attr, property), f"{name} is not a property"
    assert cls_attr.fget is not None, f"{name} has no getter"
    assert cls_attr.fset is not None, f"{name} has no setter"


# ── The 2 clamping properties stay hand-written (NOT factory) ─────────


def test_speech_threshold_db_still_clamps(vp: VadProcessor) -> None:
    """``speech_threshold_db`` must keep its R18-F14 clamp — NOT converted
    to a plain factory pass-through."""
    # Value below the floor is clamped UP to the floor.
    vp.speech_threshold_db = -100.0
    assert vp.speech_threshold_db == MIN_VAD_SPEECH_THRESHOLD_DB
    # Value above the floor passes through unchanged.
    vp.speech_threshold_db = -30.0
    assert vp.speech_threshold_db == -30.0


def test_silence_threshold_db_still_clamps(vp: VadProcessor) -> None:
    """``silence_threshold_db`` must keep its R18-F14 clamp."""
    vp.silence_threshold_db = -100.0
    assert vp.silence_threshold_db == MIN_VAD_SILENCE_THRESHOLD_DB
    vp.silence_threshold_db = -60.0
    assert vp.silence_threshold_db == -60.0


def test_threshold_db_properties_are_property_objects(vp: VadProcessor) -> None:
    """The two clamping properties must be real ``property`` descriptors
    (not factory outputs that lost the clamp)."""
    assert isinstance(type(vp).speech_threshold_db, property)
    assert isinstance(type(vp).silence_threshold_db, property)


# ── Docstrings preserved on the 3 properties that had them ────────────


@pytest.mark.parametrize(
    "name,substring",
    (
        ("calibration_prob_values", "Silero-probability samples"),
        ("vad_auto_calibrate", "Silero-probability auto-calibration"),
        ("calibration_status", "Explicit, inspectable reason"),
    ),
)
def test_factory_property_docstring_preserved(name: str, substring: str) -> None:
    """The 3 properties that had docstrings pre-refactor must keep a
    docstring (the factory accepts an optional ``doc`` argument)."""
    prop = getattr(VadProcessor, name)
    assert isinstance(prop, property)
    assert prop.__doc__ is not None
    assert substring in prop.__doc__
