"""IPC dispatch tests for config commands (get_config / set_config).

Classes:
- TestDispatchGetConfig                  — get_config dispatcher
- TestDispatchSetConfig                  — set_config dispatcher (basic)
- TestDispatchEscCancelLive              — live ESC/repaste hotkey registration
- TestDispatchSetConfigAllowlist         — SEC-002 set_config allowlist + types
- TestGetConfigRedactsSecrets            — SEC-003 get_config redacts API keys
- TestTrustedPathFieldsBlockedInSetConfig — SEC-006 trusted-path field rejection

Split out from the original monolithic tests/test_server.py (DT-37, Phase 4.5).
"""

from unittest.mock import MagicMock

import pytest

from tests.server.conftest import (  # noqa: F401
    IPCServer,
    MockApp,
    mock_app,
    server,
)


class TestDispatchGetConfig:
    def test_returns_config_dict(self, server):
        result = server._dispatch({"id": 1, "type": "get_config"})
        assert result["type"] == "config"
        assert result["id"] == 1
        data = result["data"]
        assert data["hotkey"] == "<f2>"
        assert data["model_size"] == "tiny"


class TestDispatchSetConfig:
    def test_updates_config_and_returns_ack(self, server, mock_app):
        result = server._dispatch(
            {
                "id": 1,
                "type": "set_config",
                "data": {"hotkey": "<f3>", "model_size": "large-v3-turbo"},
            }
        )
        assert result["type"] == "ack"  # may include data field
        assert mock_app.config.hotkey == "<f3>"
        assert mock_app.config.model_size == "large-v3-turbo"
        assert mock_app.config._saved is True

    def test_empty_data_acks_without_saving(self, server, mock_app):
        # G4-L-20: an empty update is a no-op — apply_config's dirty-check
        # skips save_strict() when nothing changed. The contract is
        # "ack + no disk write", not "ack + always save".
        mock_app.config._saved = False
        result = server._dispatch(
            {
                "id": 1,
                "type": "set_config",
                "data": {},
            }
        )
        assert result["type"] == "ack"  # may include data field
        assert mock_app.config._saved is False

    def test_no_data_returns_error(self, server, mock_app):
        """NEW-IPC-005: set_config with no data field must return an error,
        not silently succeed with {type: "ack"}."""
        mock_app.config._saved = False
        result = server._dispatch(
            {
                "id": 1,
                "type": "set_config",
            }
        )
        assert result["type"] == "error"
        assert "data: object" in result["data"]["message"]
        assert mock_app.config._saved is False

    def test_ignores_unknown_fields_without_crashing(self, server, mock_app):
        """set_config with unknown fields should not crash."""
        result = server._dispatch(
            {
                "id": 1,
                "type": "set_config",
                "data": {"nonexistent_field": "nope"},
            }
        )
        assert result["type"] == "ack"  # may include data field
        # G4-L-20: unknown keys are dropped, leaving a no-op update —
        # the dirty-check skips save_strict() when nothing changed.
        assert mock_app.config._saved is False

    def test_non_dict_data_returns_error(self, server, mock_app):
        """NEW-IPC-005: set_config with non-dict data must return an error,
        not silently succeed with {type: "ack"}."""
        mock_app.config._saved = False
        result = server._dispatch(
            {
                "id": 1,
                "type": "set_config",
                "data": "bad",
            }
        )
        assert result["type"] == "error"
        assert "data: object" in result["data"]["message"]
        # Config must NOT have been saved
        assert mock_app.config._saved is False
        # Config fields should not have been overwritten
        assert mock_app.config.hotkey == "<f2>"


class TestDispatchEscCancelLive:
    """Live registration of ESC cancel hotkey via set_config."""

    def test_enable_esc_cancel_calls_register_esc_hotkey(self, server, mock_app):
        """set_config with esc_cancel_enabled=true should call _register_esc_hotkey."""
        # Phase 2: service now calls `app.hotkeys.register_esc()` directly.
        mock_app.hotkeys.register_esc = MagicMock()
        mock_app.hotkeys.unregister_esc = MagicMock()

        result = server._dispatch(
            {
                "id": 1,
                "type": "set_config",
                "data": {"esc_cancel_enabled": True},
            }
        )
        assert result["type"] == "ack"  # may include data field
        mock_app.hotkeys.register_esc.assert_called_once()
        mock_app.hotkeys.unregister_esc.assert_not_called()

    def test_disable_esc_cancel_calls_unregister_esc_hotkey(self, server, mock_app):
        """set_config with esc_cancel_enabled=false should call _unregister_esc_hotkey."""
        # Phase 2: service now calls `app.hotkeys.unregister_esc()` directly.
        mock_app.hotkeys.register_esc = MagicMock()
        mock_app.hotkeys.unregister_esc = MagicMock()

        result = server._dispatch(
            {
                "id": 1,
                "type": "set_config",
                "data": {"esc_cancel_enabled": False},
            }
        )
        assert result["type"] == "ack"  # may include data field
        mock_app.hotkeys.unregister_esc.assert_called_once()
        mock_app.hotkeys.register_esc.assert_not_called()

    def test_enable_repaste_hotkey_calls_register_repaste_hotkey(self, server, mock_app):
        """set_config with repaste_hotkey should call _register_repaste_hotkey.

        Round 0: changed the test input from ``<ctrl>+<v>`` to
        ``<ctrl>+<alt>+v`` because the config_validators denylist
        (SEC-CTRL-BLOCK) now blocks pure Ctrl+letter combos that clash
        with reserved application shortcuts (Copy/Paste/Undo/Save/etc.).
        ``<ctrl>+<alt>+v`` is the default repaste_hotkey (config.py:536)
        and is also used by the passing sibling test
        test_side_effect_repaste_fires_on_repaste_hotkey.
        """
        # Phase 2: service now calls `app.hotkeys.register_repaste()` directly.
        mock_app.hotkeys.register_repaste = MagicMock()

        result = server._dispatch(
            {
                "id": 1,
                "type": "set_config",
                "data": {"repaste_hotkey": "<ctrl>+<alt>+v"},
            }
        )
        assert result["type"] == "ack"  # may include data field
        mock_app.hotkeys.register_repaste.assert_called_once()


# ── SEC-002: set_config allowlist + type/range validation ──────────────


class TestDispatchSetConfigAllowlist:
    """SEC-002: `set_config` must reject fields outside an explicit allowlist
    and validate types/ranges for fields inside it.

    These tests use the *real* `Config` dataclass (not MockConfig) so the
    allowlist is exercised against the actual schema it protects.
    """

    @pytest.fixture
    def real_config(self, tmp_path, monkeypatch):
        """Real Config instance with save() patched to a no-op (we don't
        want IPC tests touching the user's ~/.voice-typer directory)."""
        from voice_typer.server import config as config_module

        monkeypatch.setattr(config_module, "_config_dir", lambda: tmp_path)
        cfg = config_module.Config()
        cfg.save = MagicMock(return_value=True)
        return cfg

    @pytest.fixture
    def real_server(self, real_config):
        """IPCServer backed by a MockApp carrying a real Config instance."""
        app = MockApp()
        app.config = real_config
        # Pre-warm / autostart / hotkey side-effects: no-op by default.
        # Phase 2/: service.apply_config_side_effects now calls
        # `startup_tasks.sync_autostart(app)` directly (not
        # `app._sync_autostart()`); the MockApp's `hotkeys` MagicMock
        # already stubs `register_esc/unregister_esc/register_repaste`.
        return IPCServer(app)

    # ── Allowlist boundary ───────────────────────────────────────────

    def test_rejects_schema_version_even_though_it_exists(self, real_server, real_config):
        """schema_version is on Config but must NOT be mutable via IPC."""
        original = real_config.schema_version
        result = real_server._dispatch(
            {
                "id": 1,
                "type": "set_config",
                "data": {"schema_version": 999},
            }
        )
        # Silent drop — preserves the existing "unknown field" contract.
        assert result["type"] == "ack"  # may include data field
        assert real_config.schema_version == original

    def test_rejects_internal_state_field_wayland_warned(self, real_server, real_config):
        """wayland_warned is internal state, not user-tunable."""
        original = real_config.wayland_warned
        result = real_server._dispatch(
            {
                "id": 1,
                "type": "set_config",
                "data": {"wayland_warned": True},
            }
        )
        assert result["type"] == "ack"  # may include data field
        assert real_config.wayland_warned == original

    def test_rejects_onboarding_completed_via_set_config(self, real_server, real_config):
        """onboarding_completed is set by the dedicated complete_onboarding IPC,
        not by set_config."""
        original = real_config.onboarding_completed
        result = real_server._dispatch(
            {
                "id": 1,
                "type": "set_config",
                "data": {"onboarding_completed": True},
            }
        )
        assert result["type"] == "ack"  # may include data field
        assert real_config.onboarding_completed == original

    def test_rejects_trusted_path_field_qwen_model_path(self, real_server, real_config):
        """qwen_model_path is a trusted-path field (set by model download flow)."""
        original = real_config.qwen_model_path
        result = real_server._dispatch(
            {
                "id": 1,
                "type": "set_config",
                "data": {"qwen_model_path": "/etc/passwd"},
            }
        )
        assert result["type"] == "ack"
        # rejected keys are now echoed in data
        assert "qwen_model_path" in result.get("data", {}).get("rejected", [])
        assert real_config.qwen_model_path == original

    def test_rejects_trusted_path_field_parakeet_model_path(self, real_server, real_config):
        """parakeet_model_path is a trusted-path field."""
        original = real_config.parakeet_model_path
        result = real_server._dispatch(
            {
                "id": 1,
                "type": "set_config",
                "data": {"parakeet_model_path": "/tmp/evil"},
            }
        )
        assert result["type"] == "ack"
        assert "parakeet_model_path" in result.get("data", {}).get("rejected", [])
        assert real_config.parakeet_model_path == original

    def test_rejects_trusted_path_field_corrections_path(self, real_server, real_config):
        """corrections_path is a trusted-path field (set by file picker)."""
        original = real_config.corrections_path
        result = real_server._dispatch(
            {
                "id": 1,
                "type": "set_config",
                "data": {"corrections_path": "/tmp/evil.json"},
            }
        )
        assert result["type"] == "ack"
        assert "corrections_path" in result.get("data", {}).get("rejected", [])
        assert real_config.corrections_path == original

    # ── Type validation ──────────────────────────────────────────────

    def test_rejects_bool_field_with_string_value(self, real_server, real_config):
        """autostart is a bool; sending "true" must be rejected, not coerced."""
        original = real_config.autostart
        result = real_server._dispatch(
            {
                "id": 1,
                "type": "set_config",
                "data": {"autostart": "true"},
            }
        )
        assert result["type"] == "error"
        assert "autostart" in result["data"]["message"]
        assert real_config.autostart == original  # unchanged
        real_config.save.assert_not_called()  # no save on validation failure

    def test_rejects_bool_field_with_int_value(self, real_server, real_config):
        """Python bool is a subclass of int — guard against 1/0 being silently
        accepted as a bool."""
        original = real_config.autostart
        result = real_server._dispatch(
            {
                "id": 1,
                "type": "set_config",
                "data": {"autostart": 1},
            }
        )
        assert result["type"] == "error"
        assert real_config.autostart == original

    def test_rejects_int_field_with_bool_value(self, real_server, real_config):
        """max_recording_time_seconds is an int; True must not silently become 1."""
        original = real_config.max_recording_time_seconds
        result = real_server._dispatch(
            {
                "id": 1,
                "type": "set_config",
                "data": {"max_recording_time_seconds": True},
            }
        )
        assert result["type"] == "error"
        assert real_config.max_recording_time_seconds == original

    def test_rejects_int_field_with_string_value(self, real_server, real_config):
        """Int field with a string value must be rejected (no silent coercion)."""
        result = real_server._dispatch(
            {
                "id": 1,
                "type": "set_config",
                "data": {"max_recording_time_seconds": "60"},
            }
        )
        assert result["type"] == "error"

    def test_rejects_str_field_with_int_value(self, real_server, real_config):
        """hotkey is a str; sending an int must be rejected."""
        original = real_config.hotkey
        result = real_server._dispatch(
            {
                "id": 1,
                "type": "set_config",
                "data": {"hotkey": 123},
            }
        )
        assert result["type"] == "error"
        assert real_config.hotkey == original

    def test_rejects_optional_str_field_with_int_value(self, real_server, real_config):
        """microphone is Optional[str]; int must be rejected. None is OK."""
        original = real_config.microphone
        result = real_server._dispatch(
            {
                "id": 1,
                "type": "set_config",
                "data": {"microphone": 42},
            }
        )
        assert result["type"] == "error"
        assert real_config.microphone == original

    def test_accepts_none_for_optional_str_field(self, real_server, real_config):
        """microphone=None is the documented 'system default' value."""
        real_config.microphone = "device-1"
        result = real_server._dispatch(
            {
                "id": 1,
                "type": "set_config",
                "data": {"microphone": None},
            }
        )
        assert result["type"] == "ack"
        assert real_config.microphone is None

    def test_rejects_float_field_with_string_value(self, real_server, real_config):
        """stop_on_silence_seconds is a float; string must be rejected."""
        original = real_config.stop_on_silence_seconds
        result = real_server._dispatch(
            {
                "id": 1,
                "type": "set_config",
                "data": {"stop_on_silence_seconds": "120"},
            }
        )
        assert result["type"] == "error"
        assert real_config.stop_on_silence_seconds == original

    def test_accepts_int_for_float_field(self, real_server, real_config):
        """Python int is a valid float value (numeric tower)."""
        result = real_server._dispatch(
            {
                "id": 1,
                "type": "set_config",
                "data": {"stop_on_silence_seconds": 120},
            }
        )
        assert result["type"] == "ack"
        assert real_config.stop_on_silence_seconds == 120

    # ── Range validation ─────────────────────────────────────────────

    def test_rejects_negative_silence_warning_seconds(self, real_server, real_config):
        result = real_server._dispatch(
            {
                "id": 1,
                "type": "set_config",
                "data": {"silence_warning_seconds": -1.0},
            }
        )
        assert result["type"] == "error"

    def test_rejects_oversized_silence_warning_seconds(self, real_server, real_config):
        result = real_server._dispatch(
            {
                "id": 1,
                "type": "set_config",
                "data": {"silence_warning_seconds": 1_000_000.0},
            }
        )
        assert result["type"] == "error"

    def test_rejects_negative_max_recording_time_seconds(self, real_server, real_config):
        result = real_server._dispatch(
            {
                "id": 1,
                "type": "set_config",
                "data": {"max_recording_time_seconds": -5},
            }
        )
        assert result["type"] == "error"

    def test_rejects_oversized_max_recording_time_seconds(self, real_server, real_config):
        result = real_server._dispatch(
            {
                "id": 1,
                "type": "set_config",
                "data": {"max_recording_time_seconds": 10**9},
            }
        )
        assert result["type"] == "error"

    # ── Enum validation ──────────────────────────────────────────────

    def test_rejects_invalid_model_size(self, real_server, real_config):
        """model_size must be in ALLOWED_USER_MODELS — a non-registry id is not."""
        # NOTE: 'large' IS in ALLOWED_USER_MODELS (the model registry now
        # includes the generic model ids); use a value that is genuinely
        # NOT in the registry.
        original = real_config.model_size
        result = real_server._dispatch(
            {
                "id": 1,
                "type": "set_config",
                "data": {"model_size": "large-not-a-real-model"},
            }
        )
        assert result["type"] == "error"
        assert real_config.model_size == original

    def test_accepts_valid_model_size(self, real_server, real_config):
        result = real_server._dispatch(
            {
                "id": 1,
                "type": "set_config",
                "data": {"model_size": "large-v3-turbo"},
            }
        )
        assert result["type"] == "ack"  # may include data field
        assert real_config.model_size == "large-v3-turbo"

    def test_rejects_invalid_asr_backend(self, real_server, real_config):
        original = real_config.asr_backend
        result = real_server._dispatch(
            {
                "id": 1,
                "type": "set_config",
                "data": {"asr_backend": "malicious_backend"},
            }
        )
        assert result["type"] == "error"
        assert real_config.asr_backend == original

    def test_rejects_invalid_recording_mode(self, real_server, real_config):
        result = real_server._dispatch(
            {
                "id": 1,
                "type": "set_config",
                "data": {"recording_mode": "always"},
            }
        )
        assert result["type"] == "error"

    def test_rejects_invalid_theme_mode(self, real_server, real_config):
        result = real_server._dispatch(
            {
                "id": 1,
                "type": "set_config",
                "data": {"theme_mode": "neon"},
            }
        )
        assert result["type"] == "error"

    def test_rejects_invalid_tray_left_click_action(self, real_server, real_config):
        result = real_server._dispatch(
            {
                "id": 1,
                "type": "set_config",
                "data": {"tray_left_click_action": "do_nothing"},
            }
        )
        assert result["type"] == "error"

    def test_rejects_invalid_bubble_position(self, real_server, real_config):
        result = real_server._dispatch(
            {
                "id": 1,
                "type": "set_config",
                "data": {"bubble_position": "left"},
            }
        )
        assert result["type"] == "error"

    def test_rejects_invalid_bubble_behavior(self, real_server, real_config):
        result = real_server._dispatch(
            {
                "id": 1,
                "type": "set_config",
                "data": {"bubble_behavior": "sometimes"},
            }
        )
        assert result["type"] == "error"

    def test_rejects_invalid_llm_preset(self, real_server, real_config):
        result = real_server._dispatch(
            {
                "id": 1,
                "type": "set_config",
                "data": {"llm_preset": "shakespeare"},
            }
        )
        assert result["type"] == "error"

    # ── String length cap ────────────────────────────────────────────

    def test_rejects_oversized_string_field(self, real_server, real_config):
        """Defend against pathological inputs (e.g. 10 MB hotkey string)."""
        result = real_server._dispatch(
            {
                "id": 1,
                "type": "set_config",
                "data": {"hotkey": "x" * 100_000},
            }
        )
        assert result["type"] == "error"

    def test_rejects_oversized_api_key(self, real_server, real_config):
        """API keys have a generous but bounded cap."""
        result = real_server._dispatch(
            {
                "id": 1,
                "type": "set_config",
                "data": {"openai_api_key": "sk-" + "x" * 100_000},
            }
        )
        assert result["type"] == "error"

    def test_rejects_oversized_llm_api_url(self, real_server, real_config):
        """LLM API URL has a sane cap."""
        result = real_server._dispatch(
            {
                "id": 1,
                "type": "set_config",
                "data": {"llm_api_url": "https://example.com/" + "x" * 100_000},
            }
        )
        assert result["type"] == "error"

    # ── URL scheme validation (defense against SEC-002 exfiltration) ─

    def test_rejects_llm_api_url_with_javascript_scheme(self, real_server, real_config):
        """A javascript: URL would be a nonsense value but we reject any
        non-http(s) scheme to make the policy explicit."""
        result = real_server._dispatch(
            {
                "id": 1,
                "type": "set_config",
                "data": {"llm_api_url": "javascript:alert(1)"},
            }
        )
        assert result["type"] == "error"

    def test_rejects_cloud_api_url_with_file_scheme(self, real_server, real_config):
        result = real_server._dispatch(
            {
                "id": 1,
                "type": "set_config",
                "data": {"cloud_api_url": "file:///etc/passwd"},
            }
        )
        assert result["type"] == "error"

    def test_accepts_https_llm_api_url(self, real_server, real_config):
        result = real_server._dispatch(
            {
                "id": 1,
                "type": "set_config",
                "data": {"llm_api_url": "https://api.openai.com/v1/chat/completions"},
            }
        )
        assert result["type"] == "ack"  # may include data field

    def test_rejects_http_llm_api_url_non_loopback(self, real_server, real_config):
        """NEW-SEC-003 defense-in-depth: a cleartext HTTP URL for a public
        host must be rejected at set_config time, not only at call time."""
        result = real_server._dispatch(
            {
                "id": 1,
                "type": "set_config",
                "data": {"llm_api_url": "http://example.com/v1/chat/completions"},
            }
        )
        assert result["type"] == "error"

    def test_accepts_http_llm_api_url_loopback(self, real_server, real_config):
        """Loopback HTTP is still permitted (local dev servers)."""
        result = real_server._dispatch(
            {
                "id": 1,
                "type": "set_config",
                "data": {"llm_api_url": "http://localhost:11434/v1/chat/completions"},
            }
        )
        assert result["type"] == "ack"

    def test_rejects_http_cloud_api_url_non_loopback(self, real_server, real_config):
        """Same cleartext-HTTP rejection for the cloud ASR endpoint."""
        result = real_server._dispatch(
            {
                "id": 1,
                "type": "set_config",
                "data": {"cloud_api_url": "http://attacker.example.net/audio/transcriptions"},
            }
        )
        assert result["type"] == "error"

    # ── All-or-nothing on multi-field payloads ───────────────────────

    def test_multi_field_payload_rejects_all_if_any_invalid(self, real_server, real_config):
        """If one field is invalid, NO field should be applied (atomicity)."""
        original_hotkey = real_config.hotkey
        original_autostart = real_config.autostart
        result = real_server._dispatch(
            {
                "id": 1,
                "type": "set_config",
                "data": {
                    "hotkey": "<f4>",  # valid
                    "autostart": "yes",  # invalid (wrong type)
                },
            }
        )
        assert result["type"] == "error"
        # Neither field should have been applied
        assert real_config.hotkey == original_hotkey
        assert real_config.autostart == original_autostart
        real_config.save.assert_not_called()

    def test_multi_field_payload_applies_all_when_all_valid(self, real_server, real_config):
        result = real_server._dispatch(
            {
                "id": 1,
                "type": "set_config",
                "data": {
                    "hotkey": "<f4>",
                    "autostart": False,
                    "language": "fr",
                },
            }
        )
        assert result["type"] == "ack"  # may include data field
        assert real_config.hotkey == "<f4>"
        assert real_config.autostart is False
        assert real_config.language == "fr"
        real_config.save.assert_called_once()

    # ── Side-effects still fire when allowlisted fields change ────────

    def test_fast_startup_is_mutable_and_syncs_prewarm_task(self, real_server, real_config, monkeypatch):
        """PW-3: ``fast_startup`` is now a real, mutable config field.
        Sending it via ``set_config`` applies it to the config AND fires
        the ``sync_prewarm_task`` side-effect so the OS scheduled task
        is unregistered immediately (no restart needed).
        """
        from voice_typer.server import startup_tasks

        sync_prewarm_mock = MagicMock()
        monkeypatch.setattr(startup_tasks, "sync_prewarm_task", sync_prewarm_mock)
        result = real_server._dispatch(
            {
                "id": 1,
                "type": "set_config",
                "data": {"fast_startup": False},
            }
        )
        assert result["type"] == "ack"
        # Config field should now be set.
        assert real_config.fast_startup is False
        # Side effect: sync_prewarm_task should fire so the OS task is
        # unregistered immediately.
        sync_prewarm_mock.assert_called_once()

    def test_side_effect_autostart_fires_on_autostart_change(self, real_server, real_config, monkeypatch):
        # Phase 2: service now calls `startup_tasks.sync_autostart(app)` directly.
        from voice_typer.server import startup_tasks

        sync_autostart_mock = MagicMock()
        monkeypatch.setattr(startup_tasks, "sync_autostart", sync_autostart_mock)
        result = real_server._dispatch(
            {
                "id": 1,
                "type": "set_config",
                "data": {"autostart": False},
            }
        )
        assert result["type"] == "ack"  # may include data field
        sync_autostart_mock.assert_called_once()

    def test_side_effect_esc_hotkey_fires_on_esc_cancel_enabled(self, real_server, real_config):
        real_config.esc_cancel_enabled = False
        result = real_server._dispatch(
            {
                "id": 1,
                "type": "set_config",
                "data": {"esc_cancel_enabled": True},
            }
        )
        assert result["type"] == "ack"  # may include data field
        # Phase 2: service now calls `app.hotkeys.register_esc()` directly.
        real_server.app.hotkeys.register_esc.assert_called_once()

    def test_side_effect_repaste_fires_on_repaste_hotkey(self, real_server, real_config):
        result = real_server._dispatch(
            {
                "id": 1,
                "type": "set_config",
                "data": {"repaste_hotkey": "<ctrl>+<alt>+v"},
            }
        )
        assert result["type"] == "ack"  # may include data field
        # Phase 2: service now calls `app.hotkeys.register_repaste()` directly.
        real_server.app.hotkeys.register_repaste.assert_called_once()


# ── SEC-003: get_config must redact API keys ─────────────────────────────


class TestGetConfigRedactsSecrets:
    """SEC-003: ``get_config`` must NOT echo API keys back to the IPC
    client.  Any local process can connect to the loopback TCP socket
    and call ``get_config``; echoing keys in cleartext would let any
    co-located process exfiltrate them.

    The fix returns a sanitized view where secret fields are replaced
    with the literal string ``"<redacted>"`` when set, or preserved
    as the empty string when unset (so the renderer can distinguish
    "no key configured" from "key hidden")."""

    def test_set_api_key_is_redacted_in_get_config(self, server, mock_app):
        """A configured cloud_api_key must come back as '<redacted>'."""
        mock_app.config.cloud_api_key = "sk-real-secret-key-12345"
        result = server._dispatch({"id": 1, "type": "get_config"})
        assert result["type"] == "config"
        assert result["data"]["cloud_api_key"] == "<redacted>"

    def test_unset_api_key_is_empty_string(self, server, mock_app):
        """An unset key should remain '' (not '<redacted>')."""
        mock_app.config.cloud_api_key = ""
        result = server._dispatch({"id": 1, "type": "get_config"})
        assert result["data"]["cloud_api_key"] == ""

    def test_openai_api_key_redacted(self, server, mock_app):
        mock_app.config.openai_api_key = "sk-openai-real"
        result = server._dispatch({"id": 1, "type": "get_config"})
        assert result["data"]["openai_api_key"] == "<redacted>"
        assert "sk-openai-real" not in str(result["data"])

    def test_groq_api_key_redacted(self, server, mock_app):
        mock_app.config.groq_api_key = "gsk_real"
        result = server._dispatch({"id": 1, "type": "get_config"})
        assert result["data"]["groq_api_key"] == "<redacted>"

    def test_deepgram_api_key_redacted(self, server, mock_app):
        mock_app.config.deepgram_api_key = "abc-real"
        result = server._dispatch({"id": 1, "type": "get_config"})
        assert result["data"]["deepgram_api_key"] == "<redacted>"

    def test_llm_api_key_redacted(self, server, mock_app):
        mock_app.config.llm_api_key = "sk-llm-real"
        result = server._dispatch({"id": 1, "type": "get_config"})
        assert result["data"]["llm_api_key"] == "<redacted>"

    def test_non_secret_fields_preserved(self, server, mock_app):
        """Non-secret fields must come through unchanged."""
        mock_app.config.hotkey = "<f9>"
        mock_app.config.language = "fr"
        mock_app.config.cloud_api_key = "sk-real"
        result = server._dispatch({"id": 1, "type": "get_config"})
        assert result["data"]["hotkey"] == "<f9>"
        assert result["data"]["language"] == "fr"

    def test_no_real_key_value_in_response(self, server, mock_app):
        """Grep the full response: no real key value should appear
        anywhere in the serialized data."""
        mock_app.config.cloud_api_key = "sk-unique-marker-12345"
        mock_app.config.openai_api_key = "sk-another-marker-67890"
        result = server._dispatch({"id": 1, "type": "get_config"})
        serialized = str(result["data"])
        assert "sk-unique-marker-12345" not in serialized
        assert "sk-another-marker-67890" not in serialized

    def test_sanitizer_handles_missing_fields_gracefully(self):
        """If a config object doesn't have one of the secret fields
        (e.g. an older Config instance), the sanitizer must not crash."""
        from voice_typer.server.ipc_server import _sanitize_config_for_ipc

        class MinimalConfig:
            def __init__(self):
                self.hotkey = "<f2>"
                # No cloud_api_key attribute at all

        cfg = MinimalConfig()
        result = _sanitize_config_for_ipc(cfg)
        assert result["hotkey"] == "<f2>"
        # Should not have added a cloud_api_key entry that wasn't there
        assert "cloud_api_key" not in result


# ── SEC-006: trusted-path fields cannot be set via IPC ──────────────────


class TestTrustedPathFieldsBlockedInSetConfig:
    """SEC-006: standalone version of the trusted-path tests that
    doesn't depend on the class-scoped ``real_server`` / ``real_config``
    fixtures from TestDispatchSetConfigAllowlist.  Uses ``server`` and
    ``mock_app`` (function-scoped) instead."""

    def test_corrections_path_silently_dropped(self, server, mock_app):
        mock_app.config.corrections_path = None
        result = server._dispatch(
            {
                "id": 1,
                "type": "set_config",
                "data": {"corrections_path": "/etc/passwd"},
            }
        )
        assert result["type"] == "ack"  # may include data field
        assert mock_app.config.corrections_path is None  # unchanged

    def test_qwen_model_path_silently_dropped(self, server, mock_app):
        mock_app.config.qwen_model_path = None
        result = server._dispatch(
            {
                "id": 1,
                "type": "set_config",
                "data": {"qwen_model_path": "/tmp/poisoned-model"},
            }
        )
        assert result["type"] == "ack"  # may include data field
        assert mock_app.config.qwen_model_path is None  # unchanged

    def test_parakeet_model_path_silently_dropped(self, server, mock_app):
        mock_app.config.parakeet_model_path = None
        result = server._dispatch(
            {
                "id": 1,
                "type": "set_config",
                "data": {"parakeet_model_path": "/tmp/poisoned-parakeet"},
            }
        )
        assert result["type"] == "ack"  # may include data field
        assert mock_app.config.parakeet_model_path is None  # unchanged
