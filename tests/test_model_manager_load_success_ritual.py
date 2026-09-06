"""Shared post-load "success ritual" (``LoadingMixin._on_load_success``).

Six model-load paths (``load_background``, ``fallback_to_whisper``,
``try_load``, ``ensure_active_engine_loaded``'s reload branch,
``_change_model_load_phase``, ``_set_active_backend_blocking``) used to
hand-roll the same completion sequence — LRU touch + evict,
deliberate-unload flag clear, tray "Ready" message — and three of the
copies had drifted to hardcoded English strings ("Ready -- …") instead
of the localized ``state.model_manager.ready_whisper`` /
``state.model_manager.ready_other`` keys (which exist in the server's
English fallback catalog and in ALL 8 renderer locales via
``set_tray_locale``).

These tests pin:
1. the ritual's observable behavior (touch + evict + flag clear +
   localized IDLE tray message) through the composed ``ModelManager``,
2. that all six load paths route their success branch through the
   shared helper and that NO load path can regress to a hardcoded
   English "Ready -- " f-string,
3. the collapsed ``_backend_for_model_size`` mapping.
"""

from __future__ import annotations

import inspect
import threading
from unittest.mock import MagicMock

import pytest
from voice_typer.server.model_manager import ModelManager


def _make_manager(backend_name: str = "parakeet"):
    """Composed ModelManager with a mock registry whose active backend
    reports success. Mirrors ``tests/test_model_idle_unload.py``'s
    fixture pattern (mock registry swapped onto ``mm._registry``)."""
    app = MagicMock(name="app")
    app.config.asr_backend = backend_name
    app.config.model_size = "small.en"
    app.config.model_idle_unload_minutes = 0
    app._shutting_down = False
    app._pending_dictation = False
    app._thread_registry = MagicMock()
    app._config_mutation_lock = threading.RLock()

    mm = ModelManager(app)

    engine = MagicMock(name="engine")
    engine.is_loaded = True
    engine.device_info = f"{backend_name}/cpu"

    mock_registry = MagicMock(name="registry")
    mock_registry.active_name = backend_name
    mock_registry.get_active.return_value = engine
    mock_registry.get.return_value = engine
    mock_registry.load_active.return_value = True
    mock_registry.load_with_fallback.return_value = True
    mock_registry.available_backends = [backend_name]
    mm._registry = mock_registry
    mm._ensure_engine = MagicMock()

    return mm, app, engine, mock_registry


def _idle_messages(app: MagicMock) -> list[str]:
    """Return the messages the tray received for IDLE-state transitions."""
    out = []
    for c in app.tray.set_state.call_args_list:
        state = c.args[0] if c.args else c.kwargs.get("state")
        msg = c.args[1] if len(c.args) > 1 else c.kwargs.get("message", "")
        if "IDLE" in str(state):
            out.append(msg or "")
    return out


class TestOnLoadSuccessRitual:
    def test_helper_emits_localized_ready_message_for_non_whisper(self):
        """A non-whisper engine must get ``ready_other`` (localized key)
        — never a hardcoded English string and never the whisper key."""
        from voice_typer.server import i18n

        mm, app, _engine, _registry = _make_manager(backend_name="parakeet")
        mm._on_load_success("parakeet")

        expected = i18n.t("state.model_manager.ready_other", name="Parakeet")
        idle = _idle_messages(app)
        assert idle == [expected], f"unexpected IDLE tray messages: {idle}"

    def test_helper_emits_localized_ready_message_for_whisper(self):
        from voice_typer.server import i18n

        mm, app, _engine, _registry = _make_manager(backend_name="whisper")
        mm._on_load_success("whisper")

        expected = i18n.t("state.model_manager.ready_whisper", device_info="whisper/cpu")
        idle = _idle_messages(app)
        assert idle == [expected], f"unexpected IDLE tray messages: {idle}"

    def test_helper_touches_evicts_and_clears_flag(self):
        mm, _app, _engine, _registry = _make_manager(backend_name="parakeet")
        mm._deliberately_unloaded.add("parakeet")

        touch = MagicMock()
        evict = MagicMock()
        mm.touch_model = touch
        mm._evict_lru_model = evict
        mm._on_load_success("parakeet")

        touch.assert_called_once_with("parakeet")
        evict.assert_called_once_with()
        assert "parakeet" not in mm._deliberately_unloaded

    def test_helper_swallows_lru_tracking_failure(self):
        """A tracking failure must not break the load (non-fatal) — the
        tray still gets the localized ready message."""
        from voice_typer.server import i18n

        mm, app, _engine, _registry = _make_manager(backend_name="parakeet")
        mm.touch_model = MagicMock(side_effect=RuntimeError("lru boom"))
        mm._evict_lru_model = MagicMock()

        mm._on_load_success("parakeet")

        expected = i18n.t("state.model_manager.ready_other", name="Parakeet")
        assert expected in _idle_messages(app)


class TestAllLoadPathsUseSharedRitual:
    """Every load path must produce a LOCALIZED message (never a
    hardcoded English "Ready -- " f-string) and must route through the
    shared helper."""

    @pytest.mark.parametrize(
        ("mixin", "func_name"),
        [
            ("loading", "load_background"),
            ("loading", "fallback_to_whisper"),
            ("loading", "try_load"),
            ("loading", "ensure_active_engine_loaded"),
            ("change", "_change_model_load_phase"),
            ("change", "_set_active_backend_blocking"),
        ],
    )
    def test_path_delegates_to_on_load_success(self, mixin, func_name):
        """Source pin: each of the six load paths calls
        ``_on_load_success`` on its success branch, and none of them
        still contains the inlined ritual (touch/evict + direct IDLE
        ready set_state)."""
        from voice_typer.server.model_manager import _change, _loading

        owner = _loading.LoadingMixin if mixin == "loading" else _change.ChangeMixin
        src = inspect.getsource(getattr(owner, func_name))
        assert "_on_load_success(" in src, (
            f"{func_name} no longer routes its success branch through the "
            f"shared _on_load_success ritual — the copy-pasted success "
            f"ritual (and its i18n drift) is back."
        )
        for stale_token in ("_evict_lru_model()", "AppState.IDLE"):
            assert stale_token not in src, (
                f"{func_name} still inlines a success-ritual fragment "
                f"({stale_token}) instead of delegating to "
                f"_on_load_success"
            )

    def test_no_hardcoded_english_ready_remains_in_load_paths(self):
        """No model-manager module may hardcode an interpolated English
        "Ready -- " f-string — the tray message must come from the i18n
        keys (the per-path ``AppState.IDLE`` absence pin above rules out
        any non-i18n ready set_state outside the helper)."""
        from voice_typer.server.model_manager import _change, _loading

        for module in (_loading, _change):
            src = inspect.getsource(module)
            assert 'f"Ready' not in src, (
                f"{module.__name__} still hardcodes an English 'Ready -- ' "
                f"f-string — tray messages must use the localized "
                f"state.model_manager.ready_* keys"
            )

    def test_ritual_body_exists_once(self):
        """The touch+evict+clear+set_state sequence must be defined once
        (in ``_on_load_success``) — not re-copied per load path."""
        from voice_typer.server.model_manager import _loading

        assert inspect.getsource(_loading).count("def _on_load_success(") == 1, (
            "exactly one _on_load_success definition expected (the ritual owner)"
        )


class TestChangeModelLoadPhaseLocalizedSuccess:
    def test_successful_load_shows_localized_ready_message(self):
        """End-to-end through ``_change_model_load_phase``: a successful
        ``load_active`` must produce the localized ``ready_other`` tray
        message (previously the hardcoded "Ready -- Parakeet ASR" on the
        backend-switch path)."""
        from voice_typer.server import i18n
        from voice_typer.server.tray_types import AppState

        mm, app, _engine, _registry = _make_manager(backend_name="parakeet")

        failure_reason = mm._change_model_load_phase("parakeet", "parakeet")

        assert failure_reason is None, f"expected success, got failure reason: {failure_reason}"
        expected = i18n.t("state.model_manager.ready_other", name="Parakeet")
        assert expected in _idle_messages(app), (
            f"localized ready message missing from tray transitions: {app.tray.set_state.call_args_list}"
        )
        called = app.tray.set_state.call_args_list[-1]
        assert called.args[0] == AppState.IDLE


class TestEnsureActiveEngineReloadLocalizedSuccess:
    def test_reload_after_idle_unload_shows_localized_ready_message(self):
        """End-to-end through the reload-after-idle-unload branch of
        ``ensure_active_engine_loaded``: the tray must end on the
        localized ready message (previously the hardcoded English
        "Ready -- parakeet/cpu")."""
        from voice_typer.server import i18n

        mm, app, engine, _registry = _make_manager(backend_name="parakeet")
        engine.is_loaded = False

        mm.ensure_active_engine_loaded()

        expected = i18n.t("state.model_manager.ready_other", name="Parakeet")
        assert expected in _idle_messages(app), (
            f"localized ready message missing from tray transitions: {app.tray.set_state.call_args_list}"
        )


class TestBackendForModelSize:
    """The model_size → backend mapping collapsed from two verbatim
    if/elif copies into one helper."""

    @pytest.mark.parametrize(
        ("model_size", "expected"),
        [
            ("parakeet", "parakeet"),
            ("qwen", "qwen"),
            ("tiny", "whisper"),
            ("large-v3", "whisper"),
            ("large-v3-turbo", "whisper"),
            ("small.en", "whisper"),
        ],
    )
    def test_mapping(self, model_size, expected):
        from voice_typer.server.model_manager._change import _backend_for_model_size

        assert _backend_for_model_size(model_size) == expected

    def test_change_pipeline_uses_the_helper(self):
        """Both change-model entry points must go through the helper —
        the duplicated if/elif copies are gone."""
        from voice_typer.server.model_manager import _change

        for fn in (
            _change.ChangeMixin.change_model,
            _change.ChangeMixin._change_model_setattr_phase,
        ):
            src = inspect.getsource(fn)
            assert "_backend_for_model_size(" in src, (
                f"{fn.__qualname__} must use _backend_for_model_size "
                f"(the duplicated mapping copies were collapsed into it)"
            )
