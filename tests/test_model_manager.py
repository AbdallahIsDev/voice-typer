"""AC-5 regression test: ``available_backends`` @property must NOT be
called with parens.

Pre-fix bug: ``model_manager.py:363`` called
``self._registry.available_backends()`` — but ``available_backends`` is
a ``@property`` returning ``list[str]``. Calling ``()`` on the
returned list raised ``TypeError: 'list' object is not callable`` on
the all-backends-fail path, masking the diagnostic ``log.warning``
that lists attempted backends + primary backend (actionable
diagnostic info for the user / support).
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

from voice_typer.server.model_manager import ModelManager


def _make_mm_with_failing_registry() -> tuple[ModelManager, MagicMock]:
    """Construct a ModelManager whose registry's
    ``load_with_fallback`` returns falsy (simulating all backends
    failed). Returns the ModelManager and the mock app for
    inspection.
    """
    # Build a minimal mock app with the attributes ModelManager.__init__
    # and load_background read.
    app = MagicMock(name="app")
    app.config.asr_backend = "whisper"
    app.config.model_size = "tiny.en"
    app.config.device = "cpu"
    app.config.language = "en"
    app.config.beam_size = 1
    app.config.best_of = 1
    app.config.condition_on_previous_text = False
    app._shutting_down = False
    app._pending_dictation = False
    app._thread_registry = MagicMock()

    mm = ModelManager(app)

    # Replace the registry with a mock whose ``load_with_fallback``
    # returns falsy (the all-backends-fail path).
    mock_registry = MagicMock(name="registry")
    mock_registry.load_with_fallback.return_value = None  # falsy → fail path
    # ``available_backends`` is a @property — mock it as a list, NOT a
    # callable. The OLD buggy code would call this with parens and
    # raise TypeError. The AC-5 fix accesses it as a property.
    mock_registry.available_backends = ["whisper", "parakeet"]
    mock_registry.active_name = "whisper"
    mock_registry.get_active.return_value = None
    mm._registry = mock_registry

    # Stub _ensure_engine so we don't actually try to construct a real
    # TranscriptionEngine. (_sync_registry_from_fields was removed — the
    # @property setters on transcriber / _qwen_engine / _parakeet_engine
    # now keep the registry in sync automatically.)
    mm._ensure_engine = MagicMock()
    # Stub touch_model + _evict_lru_model so they don't touch LRU state.
    mm.touch_model = MagicMock()
    mm._evict_lru_model = MagicMock()

    return mm, app


class TestAC5AvailableBackendsPropertyNoParens:
    """AC-5: ``available_backends`` is a @property — must be accessed
    WITHOUT parens."""

    def test_source_does_not_call_available_backends_with_parens(self):
        """Source guard: ``load_background`` must NOT call
        ``available_backends()`` (with parens). It must access it as a
        property: ``available_backends`` (no parens)."""
        import inspect

        src = inspect.getsource(ModelManager.load_background)
        # The buggy form: ``self._registry.available_backends()`` with
        # parens. We strip whitespace inside the parens to be robust
        # against formatting.
        assert "available_backends()" not in src, (
            "AC-5 regression: load_background calls "
            "self._registry.available_backends() with parens — but "
            "available_backends is a @property. Calling it with parens "
            "raises TypeError: 'list' object is not callable, masking "
            "the diagnostic log.warning on the all-backends-fail path."
        )
        # The fixed form: property access without parens.
        assert "available_backends" in src, (
            "AC-5: load_background must access "
            "self._registry.available_backends (no parens) to list "
            "attempted backends in the diagnostic log.warning."
        )

    def test_all_backends_fail_emits_warning_with_backend_names(self, caplog):
        """End-to-end: when ``load_with_fallback`` returns falsy (all
        backends failed), the diagnostic ``log.warning`` MUST be
        emitted with the backend names — NOT a ``TypeError`` from
        calling the ``available_backends`` property with parens.

        Pre-fix: this test would fail with ``TypeError: 'list' object
        is not callable`` raised from inside ``load_background`` (the
        ``except Exception`` block would catch it and log
        ``[STARTUP] Background model load crashed`` — masking the
        diagnostic ``log.warning`` that lists the attempted backends).
        """
        mm, app = _make_mm_with_failing_registry()

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.model_manager"):
            mm.load_background()

        # The diagnostic log.warning MUST be present (it lists the
        # attempted backends + primary). Pre-fix, the TypeError raised
        # by ``available_backends()`` masked this warning.
        diagnostic_warnings = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING and "All backends failed to load" in r.getMessage()
        ]
        assert diagnostic_warnings, (
            "AC-5: when all backends fail to load, load_background must "
            "emit a log.warning listing the attempted backends + primary. "
            "Pre-fix, this warning was masked by a TypeError raised when "
            "calling available_backends() (a @property) with parens."
        )
        # The warning message must include the backend names from the
        # (mocked) ``available_backends`` property.
        warning_msg = diagnostic_warnings[0].getMessage()
        assert "whisper" in warning_msg and "parakeet" in warning_msg, (
            f"AC-5: the diagnostic log.warning must list the attempted "
            f"backends (whisper, parakeet). Got: {warning_msg!r}"
        )
        # The primary backend name must also be present.
        assert "primary=whisper" in warning_msg, (
            f"AC-5: the diagnostic log.warning must include the primary backend name. Got: {warning_msg!r}"
        )

    def test_all_backends_fail_does_not_raise_typeerror(self, caplog):
        """AC-5: the all-backends-fail path must NOT raise
        ``TypeError: 'list' object is not callable`` (the pre-fix bug
        from calling ``available_backends()`` with parens)."""
        mm, app = _make_mm_with_failing_registry()

        # Capture all log records at any level — we want to assert the
        # ``Background model load crashed`` exception log is NOT present
        # (that would indicate load_background hit the outer except).
        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.model_manager"):
            mm.load_background()

        crashed_logs = [r for r in caplog.records if "Background model load crashed" in r.getMessage()]
        assert not crashed_logs, (
            "AC-5: load_background's outer ``except Exception`` caught "
            "an exception (logged as 'Background model load crashed'). "
            "Pre-fix, this was a TypeError from calling available_backends() "
            "with parens. The fixed code must reach the diagnostic "
            "log.warning without raising."
        )
