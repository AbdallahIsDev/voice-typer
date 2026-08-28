"""Third-party logger silencing coverage (AP-48).

``_THIRD_PARTY_LOGGER_LEVELS`` in
``voice_typer/server/log/__init__.py`` is the authoritative map of
every third-party logger the app depends on (directly or transitively)
that is pinned to WARNING so external-library noise never floods the
rotating file.

These tests pin:
- the map covers the known-chatty set from the finding (urllib3 /
  requests / httpx / httpcore / websockets / keyring / sounddevice /
  PIL / numpy / torch / onnxruntime / faster_whisper / ctranslate2 /
  huggingface_hub / transformers / pystray / asyncio)
- applying the map resolves every mapped logger to <= WARNING with no
  leftover handlers
- the silencing is applied even when ``sys.stderr`` is None — the
  pythonw.exe frozen-exe path that the old nested placement skipped
"""

from __future__ import annotations

import logging
import sys

from voice_typer.server import log as log_module

# Known-chatty third-party loggers from the AP-48 finding. Each must be
# present in the authoritative map.
KNOWN_CHATTY = [
    "urllib3",
    "urllib3.connectionpool",
    "requests",
    "httpx",
    "httpcore",
    "websockets",
    "keyring",
    "sounddevice",
    "PIL",
    "numpy",
    "torch",
    "onnxruntime",
    "faster_whisper",
    "ctranslate2",
    "huggingface_hub",
    "transformers",
    "pystray",
    "asyncio",
]


def test_map_covers_known_chatty_loggers() -> None:
    missing = [name for name in KNOWN_CHATTY if name not in log_module._THIRD_PARTY_LOGGER_LEVELS]
    assert not missing, f"known chatty loggers missing from _THIRD_PARTY_LOGGER_LEVELS: {missing}"


def test_apply_sets_warning_and_clears_handlers() -> None:
    log_module._apply_third_party_logger_levels()
    for name, level in log_module._THIRD_PARTY_LOGGER_LEVELS.items():
        logger = logging.getLogger(name)
        assert logger.level <= level, f"{name} resolved to level {logger.level}, expected <= {level}"
        assert logger.handlers == [], f"{name} still has handlers attached: {logger.handlers}"
        assert logger.propagate is True, f"{name} has propagate={logger.propagate}"


def test_apply_applies_when_stderr_is_none(monkeypatch) -> None:
    """The silencing must not depend on ``sys.stderr`` being present.

    pythonw.exe runs with ``sys.stderr is None``; the old placement of
    the silencing nested it inside the ``if sys.stderr is not None:``
    stream-handler block, silently skipping it on that path.
    """
    monkeypatch.setattr(sys, "stderr", None)
    log_module._apply_third_party_logger_levels()
    assert logging.getLogger("urllib3").level <= logging.WARNING
    assert logging.getLogger("websockets").level <= logging.WARNING
    assert logging.getLogger("keyring").level <= logging.WARNING
