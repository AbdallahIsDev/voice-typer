"""Shared post-load \"success ritual\" (``LoadingMixin._on_load_success``)."""

from __future__ import annotations

import inspect
import threading
from unittest.mock import MagicMock

import pytest
from voice_typer.server.model_manager import ModelManager


def _make_manager(backend_name: str = "parakeet"):
    """Composed ModelManager with a mock registry whose active backend"""
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
        """A non-whisper engine must get ``ready_other`` (localized key)"""
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
        """A tracking failure must not break the load (non-fatal), the"""
        from voice_typer.server import i18n

        mm, app, _engine, _registry = _make_manager(backend_name="parakeet")
        mm.touch_model = MagicMock(side_effect=RuntimeError("lru boom"))
        mm._evict_lru_model = MagicMock()

        mm._on_load_success("parakeet")

        expected = i18n.t("state.model_manager.ready_other", name="Parakeet")
        assert expected in _idle_messages(app)


class TestAllLoadPathsUseSharedRitual:
    """shared helper."""

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
        """Source pin: each of the six load paths calls"""
        from voice_typer.server.model_manager import _change, _loading

        owner = _loading.LoadingMixin if mixin == "loading" else _change.ChangeMixin
        src = inspect.getsource(getattr(owner, func_name))
        assert "_on_load_success(" in src, (
            f"{func_name} no longer routes its success branch through the "
            f"shared _on_load_success ritual, the copy-pasted success "
            f"ritual (and its i18n drift) is back."
        )
        for stale_token in ("_evict_lru_model()", "AppState.IDLE"):
            assert stale_token not in src, (
                f"{func_name} still inlines a success-ritual fragment "
                f"({stale_token}) instead of delegating to "
                f"_on_load_success"
            )

    def test_no_hardcoded_english_ready_remains_in_load_paths(self):
        """No model-manager module may hardcode an interpolated English"""
        from voice_typer.server.model_manager import _change, _loading

        for module in (_loading, _change):
            src = inspect.getsource(module)
            assert 'f"Ready' not in src, (
                f"{module.__name__} still hardcodes an English 'Ready | ' "
                f"f-string, tray messages must use the localized "
                f"state.model_manager.ready_* keys"
            )

    def test_ritual_body_exists_once(self):
        """The touch+evict+clear+set_state sequence must be defined once"""
        from voice_typer.server.model_manager import _loading

        assert inspect.getsource(_loading).count("def _on_load_success(") == 1, (
            "exactly one _on_load_success definition expected (the ritual owner)"
        )


class TestChangeModelLoadPhaseLocalizedSuccess:
    def test_successful_load_shows_localized_ready_message(self):
        """End-to-end through ``_change_model_load_phase``: a successful"""
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
        """\"Ready -- parakeet/cpu\")."""
        from voice_typer.server import i18n

        mm, app, engine, _registry = _make_manager(backend_name="parakeet")
        engine.is_loaded = False

        mm.ensure_active_engine_loaded()

        expected = i18n.t("state.model_manager.ready_other", name="Parakeet")
        assert expected in _idle_messages(app), (
            f"localized ready message missing from tray transitions: {app.tray.set_state.call_args_list}"
        )


class TestBackendForModelSize:
    """The model_size → backend mapping collapsed from two verbatim"""

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
        """Both change-model entry points must go through the helper —"""
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
