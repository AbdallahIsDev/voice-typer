"""Config side-effect dispatcher (registered handlers, not an if-chain).

NOTE: see docs/code-notes/security-config.md#config-preset-handlers
"""

from __future__ import annotations

import contextlib
import json
import logging
from dataclasses import dataclass
from typing import Any, Protocol, TypedDict

from voice_typer.server.branding import APP_NAME

# hotkeys.restart() fallback must use the canonical DEFAULT_HOTKEY.
from voice_typer.server.config import DEFAULT_HOTKEY

log = logging.getLogger(__name__)

# One-shot flag: the ACL-enforcement-failure tray toast fires at most
_acl_enforcement_failure_notified = False


def _maybe_notify_acl_enforcement_failure(app: Any) -> None:
    """One-time tray warning when Windows ACL enforcement failed; at most once per process."""
    global _acl_enforcement_failure_notified
    if _acl_enforcement_failure_notified:
        return
    try:
        from voice_typer.server.config._saving import acl_enforcement_failures
    except Exception:
        return
    if not acl_enforcement_failures:
        return
    _acl_enforcement_failure_notified = True
    notify = getattr(getattr(app, "tray", None), "notify", None)
    if not callable(notify):
        log.warning(
            "[CONFIG] ACL enforcement failed for %s; plaintext secrets "
            "may be readable by other local users (tray notify unavailable)",
            sorted(acl_enforcement_failures),
        )
        return
    try:
        notify(
            APP_NAME,
            "Could not lock down config file permissions. API keys may be readable by other users on this PC.",
        )
    except Exception:
        log.debug("[CONFIG] tray.notify for ACL failure also failed", exc_info=True)


def _notify_side_effect_failure(app: Any, field: str, exc: BaseException) -> None:
    """Toast a config side-effect failure; on-disk config already changed so runtime mismatch needs a signal."""
    notify = getattr(getattr(app, "tray", None), "notify", None)
    if callable(notify):
        try:
            # Truncate long engine errors so the toast stays readable.
            msg = str(exc)
            if len(msg) > 200:
                msg = msg[:197] + "..."
            notify(APP_NAME, f"Could not apply {field} change: {msg}")
        except Exception:
            # DEBUG avoids a notification-failure loop; caller already logged at WARNING.
            log.debug(
                "[CONFIG] tray.notify for side-effect failure also failed (field=%s)",
                field,
                exc_info=True,
            )
    else:
        log.debug(
            "[CONFIG] app.tray.notify not available; cannot surface side-effect failure to user (field=%s)",
            field,
        )


def _json_dumps_sorted(obj: Any) -> str:
    """Stable JSON serialization for state comparison."""
    return json.dumps(obj, sort_keys=True, default=str)


# Audio-filter config keys; module-level frozenset (built once at import).
_FILTER_CHAIN_KEYS = frozenset(
    {
        # Preset
        "audio_preset",
        # Individual filter toggles
        "noise_filter_enabled",
        "noise_filter_highpass",
        "noise_filter_gate",
        "noise_filter_rnnoise",
        "noise_filter_post_capture",
        "noise_filter_eq",
        "noise_filter_compressor",
        "noise_filter_limiter",
        "noise_filter_notch",
        # Noise suppressor backend
        "noise_suppression_method",
        # Filter parameters
        "noise_filter_highpass_cutoff_hz",
        "noise_filter_gate_hold_ms",
        "noise_filter_gate_open_threshold_db",
        "noise_filter_gate_close_threshold_db",
        "noise_filter_gate_attack_ms",
        "noise_filter_gate_release_ms",
        "noise_filter_eq_low_db",
        "noise_filter_eq_mid_db",
        "noise_filter_eq_high_db",
        "noise_filter_compressor_threshold_db",
        "noise_filter_compressor_ratio",
        "noise_filter_compressor_attack_ms",
        "noise_filter_compressor_release_ms",
        "noise_filter_compressor_output_gain_db",
        "noise_filter_limiter_ceiling_db",
        "noise_filter_limiter_release_ms",
        "noise_filter_notch_frequency_hz",
    }
)


# NOTE: see docs/code-notes/security-config.md#config-preset-handlers
_PRESET_OVERRIDE_KEYS: frozenset[str] = frozenset(
    {
        "noise_filter_highpass",
        "noise_suppression_method",
        "noise_filter_gate",
        "noise_filter_eq",
        "noise_filter_compressor",
        "noise_filter_limiter",
        "noise_filter_notch",
    }
)

_AUDIO_FILTER_KEYS = (
    "noise_filter_enabled",
    "noise_filter_highpass",
    "noise_filter_gate",
    "noise_filter_rnnoise",
    "noise_filter_post_capture",
)


# Sentinel for "this Config field did not exist before setattr".
_MISSING = object()


# This replaces the bare ``dict`` annotations on the


class SideEffectStatus(TypedDict):
    """Side-effect status dict returned by :meth:`ConfigApplier.apply_config`"""

    autostart_status: dict[str, Any] | None
    prewarm_status: dict[str, Any] | None


def to_filter_dict(config: Any) -> dict[str, Any]:
    """build the audio-filter settings dict from a Config.

    Returns
    """
    import dataclasses

    from voice_typer.server.config import Config as _ConfigClass

    _fields = _ConfigClass.__dataclass_fields__
    result: dict[str, Any] = {}
    for name, field_info in _fields.items():
        if not (name.startswith("noise_filter_") or name.startswith("noise_suppression_") or name == "audio_preset"):
            continue
        default = field_info.default
        if default is dataclasses.MISSING:
            factory = field_info.default_factory
            default = factory() if factory is not dataclasses.MISSING else None
        result[name] = getattr(config, name, default)
    return result


def _apply_audio_preset(preset: str) -> dict[str, Any]:
    """ADR 0007: Map an audio preset name to individual filter settings.

    Returns:
    """
    from voice_typer.server.audio_presets import (
        PRESET_AUTO,
        PRESET_OFF,
        get_preset_filters,
    )

    # Map legacy preset names
    legacy_map = {"recommended": PRESET_AUTO, "none": PRESET_OFF}
    normalized = legacy_map.get(preset, preset)
    return get_preset_filters(normalized)


# Registered ConfigSideEffect handlers. Order matters:


@dataclass
class SideEffectContext:
    """Context passed to each registered :class:`ConfigSideEffect` handler."""

    app: Any
    config: Any
    updates: dict
    status: SideEffectStatus


class ConfigSideEffect(Protocol):
    """handler in a defensive try/except as well, so a buggy handler"""

    #: Short identifier used in log messages + tray notifications.
    name: str

    def applies(self, updates: dict) -> bool:
        """Return True if this handler should run for the given updates."""
        ...

    def apply(self, ctx: SideEffectContext) -> None:
        """is a defensive net, not the primary error path."""
        ...


class _AutostartSyncHandler:
    """Sync OS autostart entry when ``autostart`` config changes."""

    name = "autostart"

    def applies(self, updates: dict) -> bool:
        return "autostart" in updates

    def apply(self, ctx: SideEffectContext) -> None:
        app = ctx.app
        try:
            # Step 2: invoke startup_tasks directly. The
            from voice_typer.server import startup_tasks

            ctx.status["autostart_status"] = startup_tasks.sync_autostart(app)
        except Exception as e:
            log.warning("Failed to sync autostart: %s", e)
            ctx.status["autostart_status"] = {"registered": False, "error": str(e)}
            # surface the side-effect failure to the user via
            _notify_side_effect_failure(app, "autostart", e)


class _PrewarmSyncHandler:
    """Sync the prewarm scheduled task when ``fast_startup`` changes."""

    name = "fast_startup"

    def applies(self, updates: dict) -> bool:
        return "fast_startup" in updates

    def apply(self, ctx: SideEffectContext) -> None:
        app = ctx.app
        updates = ctx.updates
        try:
            from voice_typer.server import startup_tasks

            ctx.status["prewarm_status"] = startup_tasks.sync_prewarm_task(app)
            log.info(
                "[SERVICE] Prewarm task synced after fast_startup change (fast_startup=%s)",
                bool(updates.get("fast_startup")),
            )
        except Exception as e:
            log.warning("Failed to sync prewarm task: %s", e)
            ctx.status["prewarm_status"] = {"registered": False, "error": str(e)}
            # surface the prewarm task sync failure to the
            _notify_side_effect_failure(app, "fast_startup", e)


class _EscHotkeyHandler:
    """Register/unregister ESC hotkey when ``esc_cancel_enabled`` changes."""

    name = "esc_cancel_enabled"

    def applies(self, updates: dict) -> bool:
        return "esc_cancel_enabled" in updates

    def apply(self, ctx: SideEffectContext) -> None:
        app = ctx.app
        updates = ctx.updates
        try:
            if updates["esc_cancel_enabled"]:
                app.hotkeys.register_esc()
            else:
                app.hotkeys.unregister_esc()
        except Exception as e:
            log.warning("Failed to sync ESC hotkey: %s", e)
            # surface the ESC hotkey sync failure to the
            _notify_side_effect_failure(app, "esc_cancel_enabled", e)


class _RepasteHotkeyHandler:
    """Re-register repaste hotkey when ``repaste_hotkey`` changes."""

    name = "repaste_hotkey"

    def applies(self, updates: dict) -> bool:
        return "repaste_hotkey" in updates

    def apply(self, ctx: SideEffectContext) -> None:
        app = ctx.app
        try:
            app.hotkeys.register_repaste()
        except Exception as e:
            log.warning("Failed to sync repaste hotkey: %s", e)
            # surface the repaste hotkey sync failure to
            _notify_side_effect_failure(app, "repaste_hotkey", e)


class _DictationHotkeyHandler:
    """Re-register dictation hotkey when ``recording_mode`` or ``hotkey`` changes."""

    name = "hotkey"

    def applies(self, updates: dict) -> bool:
        return "recording_mode" in updates or "hotkey" in updates

    def apply(self, ctx: SideEffectContext) -> None:
        app = ctx.app
        config = ctx.config
        # snapshot the previous hotkey so we can restore it
        old_hotkey = getattr(config, "hotkey", None)
        try:
            # use ``DEFAULT_HOTKEY`` (the canonical platform default
            app.hotkeys.restart(getattr(config, "hotkey", DEFAULT_HOTKEY))
            log.info(
                "[SERVICE] Re-registered hotkey after recording_mode/hotkey change (mode=%s)",
                getattr(config, "recording_mode", "toggle"),
            )
        except Exception as e:
            log.warning("Failed to re-register hotkey after mode change: %s", e)
            # restore previous hotkey + re-save so a
            if old_hotkey is not None:
                try:
                    config.hotkey = old_hotkey
                    save_fn = getattr(config, "save", None)
                    if callable(save_fn):
                        save_fn()
                    log.info(
                        "[SERVICE] Restored hotkey to %r after restart failure",
                        old_hotkey,
                    )
                except Exception:
                    log.warning(
                        "[SERVICE] Failed to restore hotkey after restart failure",
                        exc_info=True,
                    )


class _TrayLeftClickHandler:
    """Invalidate tray menu cache when ``tray_left_click_action`` changes."""

    name = "tray_left_click_action"

    def applies(self, updates: dict) -> bool:
        return "tray_left_click_action" in updates

    def apply(self, ctx: SideEffectContext) -> None:
        app = ctx.app
        updates = ctx.updates
        try:
            app.tray.invalidate_menu_cache()
            log.info(
                "[SERVICE] Tray left-click action updated to: %s",
                updates["tray_left_click_action"],
            )
        except Exception as e:
            log.warning("Failed to update tray left-click action: %s", e)
            # surface the tray left-click action update
            _notify_side_effect_failure(app, "tray_left_click_action", e)


class _NotificationsHandler:
    """Toggle tray notifications when ``show_notifications`` changes.

    BUGFIX: show_notifications changes were not applied until restart.
    """

    name = "show_notifications"

    def applies(self, updates: dict) -> bool:
        return "show_notifications" in updates

    def apply(self, ctx: SideEffectContext) -> None:
        app = ctx.app
        updates = ctx.updates
        try:
            app.tray.set_notifications_enabled(bool(updates["show_notifications"]))
            log.info(
                "[SERVICE] Notifications %s",
                "enabled" if updates["show_notifications"] else "disabled",
            )
        except Exception as e:
            log.warning("Failed to update notifications: %s", e)
            # surface the notifications update failure to
            _notify_side_effect_failure(app, "show_notifications", e)


class _BubbleBehaviorHandler:
    """Apply bubble visibility change when ``bubble_behavior`` changes.

    BUGFIX: bubble_behavior changes were not applied until restart.
    """

    name = "bubble_behavior"

    def applies(self, updates: dict) -> bool:
        return "bubble_behavior" in updates

    def apply(self, ctx: SideEffectContext) -> None:
        app = ctx.app
        updates = ctx.updates
        try:
            behavior = updates["bubble_behavior"]
            if behavior == "always_visible":
                try:
                    if hasattr(app, "_waveform_bubble"):
                        app._waveform_bubble.show()
                except Exception:
                    # previously `except Exception: pass`
                    log.debug(
                        "[SERVICE] Failed to show waveform bubble after bubble_behavior change",
                        exc_info=True,
                    )
            elif behavior == "show_on_record":
                # Hide bubble immediately when switching away from always_visible
                try:
                    if hasattr(app, "_waveform_bubble") and app._waveform_bubble.visible:
                        app._waveform_bubble.hide()
                except Exception:
                    # same as above, log at debug so the
                    log.debug(
                        "[SERVICE] Failed to hide waveform bubble after bubble_behavior change",
                        exc_info=True,
                    )
            log.info("[SERVICE] Bubble behavior updated to: %s", behavior)
        except Exception as e:
            log.warning("Failed to update bubble behavior: %s", e)
            # surface the bubble behavior update failure to
            _notify_side_effect_failure(app, "bubble_behavior", e)


class _VolumeDuckPollHandler:
    """Config dataclass and from ``IPC_CONFIG_ALLOWLIST``, so the condition"""

    name = "volume_duck_smart_poll_interval_ms"

    def applies(self, updates: dict) -> bool:
        return "volume_duck_smart_poll_interval_ms" in updates

    def apply(self, ctx: SideEffectContext) -> None:
        app = ctx.app
        updates = ctx.updates
        try:
            if hasattr(app, "_volume_ducker"):
                app._volume_ducker.set_smart_duck_poll_interval(int(updates["volume_duck_smart_poll_interval_ms"]))
        except Exception as e:
            log.warning("Failed to update smart duck poll interval: %s", e)
            # surface the smart duck poll interval update
            _notify_side_effect_failure(app, "volume_duck_smart_poll_interval_ms", e)


class _AudioPresetHandler:
    """Apply audio preset (map preset name → filter toggles) when ``audio_preset`` changes."""

    name = "audio_preset"

    def applies(self, updates: dict) -> bool:
        return "audio_preset" in updates

    def apply(self, ctx: SideEffectContext) -> None:
        app = ctx.app
        config = ctx.config
        updates = ctx.updates
        try:
            preset = updates["audio_preset"]
            preset_filters = _apply_audio_preset(preset)
            # Set individual filter toggles from the preset
            for k, v in preset_filters.items():
                setattr(config, k, v)
            # Sync the legacy noise_filter_enabled flag so downstream
            config.noise_filter_enabled = preset != "off"
            # Log the preset NAME only, the full ``preset_filters`` dict
            log.info("[SERVICE] Applied audio preset '%s'", preset)
        except Exception as e:
            log.warning("Failed to apply audio preset: %s", e)
            # surface the audio preset apply failure to the
            _notify_side_effect_failure(app, "audio_preset", e)


class _FilterChainHandler:
    """Rebuild dictation AudioProcessor + sync level monitor when any filter-chain key changes."""

    name = "noise_filter_chain"

    def applies(self, updates: dict) -> bool:
        return bool(_FILTER_CHAIN_KEYS & updates.keys())

    def apply(self, ctx: SideEffectContext) -> None:
        app = ctx.app
        config = ctx.config
        # ADR 0007: rebuild the dictation processor (the main fix).
        try:
            if hasattr(app, "_rebuild_audio_processor"):
                app._rebuild_audio_processor()
        except Exception as e:
            log.warning("Failed to rebuild dictation audio processor: %s", e)
            # surface the audio-processor rebuild failure
            _notify_side_effect_failure(app, "noise_filter_chain", e)

        # Also sync the live level bar + mic test processors so
        try:
            from voice_typer.server.level_monitor import (
                update_level_processor,
                update_test_filters,
            )

            # use the shared helper instead of an inline
            filters_dict = to_filter_dict(config)
            update_level_processor(filters_dict)
            update_test_filters(filters_dict)
        except Exception as e:
            log.warning("Failed to sync level bar processor: %s", e)
            # surface the level bar processor sync failure
            _notify_side_effect_failure(app, "level_bar_filters", e)


class ConfigApplier:
    """Owns the post-config-update side-effect dispatch."""

    def __init__(self, service: Any) -> None:
        self._service = service
        self._app = service._app
        # Build the handler list at construction time. Each handler is
        self._side_effect_handlers: list[ConfigSideEffect] = [
            _AutostartSyncHandler(),
            _PrewarmSyncHandler(),
            _EscHotkeyHandler(),
            _RepasteHotkeyHandler(),
            _DictationHotkeyHandler(),
            _TrayLeftClickHandler(),
            _NotificationsHandler(),
            _BubbleBehaviorHandler(),
            _VolumeDuckPollHandler(),
            _AudioPresetHandler(),
            _FilterChainHandler(),
        ]

    def apply_config_side_effects(self, updates: dict) -> SideEffectStatus:
        """defensive net for handler bugs (``applies()`` raising, etc.)
        Side-effect status dict with the shape::
        """
        app = self._app
        config = app.config

        # accumulate side-effect statuses for the renderer.
        side_effect_status: SideEffectStatus = {
            "autostart_status": None,
            "prewarm_status": None,
        }

        ctx = SideEffectContext(
            app=app,
            config=config,
            updates=updates,
            status=side_effect_status,
        )

        for handler in self._side_effect_handlers:
            try:
                if handler.applies(updates):
                    handler.apply(ctx)
            except Exception as e:
                # Defensive: each handler is expected to catch its own
                handler_name = getattr(handler, "name", type(handler).__name__)
                log.warning(
                    "[SERVICE] Side-effect handler %s raised unexpectedly: %s",
                    handler_name,
                    e,
                    exc_info=True,
                )
                _notify_side_effect_failure(app, handler_name, e)

        # return the accumulated side-effect statuses so
        return side_effect_status

    @staticmethod
    def _empty_side_effect_status() -> SideEffectStatus:
        """Stable all-``None`` status dict for early-raise / no-sync paths."""
        return {
            "autostart_status": None,
            "prewarm_status": None,
        }

    def _maybe_autoswitch_audio_preset(self, updates: dict) -> dict:
        """Auto-switch ``audio_preset`` to ``"custom"`` for individual toggles."""
        if "audio_preset" in updates:
            return updates
        individual_overrides = _PRESET_OVERRIDE_KEYS & updates.keys()
        if not individual_overrides:
            return updates
        current_preset = getattr(self._app.config, "audio_preset", "custom")
        if current_preset == "custom":
            return updates
        log.info(
            "[CONFIG] individual filter toggles %s set via "
            "IPC while audio_preset=%r, auto-switching "
            "audio_preset to 'custom' so the user's toggle "
            "survives the next Config.load() (which would "
            "otherwise re-apply the preset and revert it)",
            sorted(individual_overrides),
            current_preset,
        )
        return {**updates, "audio_preset": "custom"}

    def _setattr_updates(self, app: Any, updates: dict) -> list[tuple[str, Any]]:
        """Set each validated key onto Config, with reverse-order rollback."""
        set_keys: list[tuple[str, Any]] = []
        try:
            for k, v in updates.items():
                old_value = getattr(app.config, k, _MISSING)
                set_keys.append((k, old_value))
                setattr(app.config, k, v)
        except Exception:
            # Restore pre-loop values for keys we already set, in
            for k, old_value in reversed(set_keys):
                try:
                    if old_value is not _MISSING:
                        setattr(app.config, k, old_value)
                except Exception:
                    log.warning(
                        "[SERVICE] failed to restore config key %s during setattr rollback",
                        k,
                        exc_info=True,
                    )
            raise
        return set_keys

    def _maybe_invalidate_llm_polisher(self, app: Any, updates: dict) -> None:
        """Drop the cached LLMPolisher when any polish credential changes."""
        from voice_typer.server import credential_store as _credential_store

        _polish_credential_fields = set(_credential_store.PROVIDER_TO_CONFIG_FIELD.values())
        if any(k.startswith("llm_") or k in _polish_credential_fields for k in updates):
            with contextlib.suppress(Exception):
                app._llm_polisher = None

    def _route_secrets_post_save(self, app: Any, updates: dict) -> None:
        """Redundant keychain routing for the no-keyring plaintext path."""
        if getattr(app.config, "_secrets_routed_in_save", True):
            return
        try:
            from voice_typer.server import credential_store

            for k, v in list(updates.items()):
                provider = credential_store.CONFIG_FIELD_TO_PROVIDER.get(k)
                if provider is None:
                    continue
                credential_store.store_secret(provider, v)
        except Exception as exc:
            log.warning(
                "[SERVICE] credential_store post-save route "
                "failed: %s, secret may not be in keychain (will "
                "fall back to plaintext in config.json on next save)",
                exc,
            )

    def _save_updates_strict(
        self,
        app: Any,
        updates: dict,
        set_keys: list[tuple[str, Any]],
    ) -> None:
        """Dirty-check + ``save_strict`` + save-failure rollback."""
        post_values = {k: getattr(app.config, k, _MISSING) for k in updates}
        pre_values = dict(set_keys)
        state_unchanged = pre_values == post_values
        if state_unchanged:
            log.debug("[SERVICE] apply_config detected no state change, skipping save_strict()")
            return
        try:
            app.config.save_strict()
        except Exception:
            # Restore in-memory snapshot under the same lock, then re-run side-effects with original values.
            for k, old_value in set_keys:
                try:
                    setattr(app.config, k, old_value)
                except Exception:
                    log.warning(
                        "[SERVICE] failed to restore config key %s during save_strict rollback",
                        k,
                        exc_info=True,
                    )
            # Re-run side-effects with the restored values.
            old_updates = dict(set_keys)
            if old_updates:
                try:
                    self.apply_config_side_effects(old_updates)
                except Exception:
                    log.warning(
                        "[SERVICE] failed to re-run side-effects during save_strict rollback",
                        exc_info=True,
                    )
            raise
        self._route_secrets_post_save(app, updates)

    def _maybe_refresh_clipboard(self, app: Any, updates: dict) -> None:
        """ADR-0010 §8.3b: propagate clipboard config changes live; failures log at WARNING."""
        clipboard_keys = {
            "clipboard_save_restore",
            "clipboard_restore_delay_ms",
            "paste_on_stop",
        }
        if not (clipboard_keys & set(updates.keys())):
            return
        try:
            app.clipboard.refresh_config(app.config)
        except Exception as exc:
            log.warning(
                "[SERVICE] clipboard.refresh_config failed: %s, "
                "clipboard config changes will not take effect until restart",
                exc,
            )

    def _post_save_tray_cleanup(self, app: Any) -> None:
        """Invalidate the tray menu cache and surface any ACL warning."""
        try:
            app.tray.invalidate_menu_cache()
        except Exception:
            log.debug("[SERVICE] tray.invalidate_menu_cache failed", exc_info=True)
        _maybe_notify_acl_enforcement_failure(app)

    def apply_config(self, updates: dict) -> SideEffectStatus:
        """RACE-011: holds the app's config-mutation lock for the full
        ``IPC_CONFIG_ALLOWLIST`` (SEC-002 defense-in-depth).
        """
        # SEC-002 defense-in-depth: even though the IPC
        # runtime, defeating SEC-002.
        from voice_typer.server.config_validators import IPC_CONFIG_ALLOWLIST

        _unknown = set(updates) - IPC_CONFIG_ALLOWLIST.keys()
        if _unknown:
            raise ValueError(
                f"SEC-002 violation: apply_config received "
                f"non-allowlisted keys {sorted(_unknown)}; the IPC "
                f"set_config handler should have dropped these via "
                f"validate_config_update. Internal callers must only "
                f"pass IPC_CONFIG_ALLOWLIST keys."
            )
        app = self._app
        # (session-3): capture the side-effect status dict for
        side_effect_status: SideEffectStatus = self._empty_side_effect_status()
        # + : snapshot pre-setattr Config state. Used for
        with app._config_mutation_lock:
            updates = self._maybe_autoswitch_audio_preset(updates)
            set_keys = self._setattr_updates(app, updates)
            self._maybe_invalidate_llm_polisher(app, updates)
            # Apply side effects inside the lock so Config mutations
            side_effect_status = self.apply_config_side_effects(updates)
            # ``save_strict`` raises RuntimeError if ``save()`` returned
            self._save_updates_strict(app, updates, set_keys)
            self._maybe_refresh_clipboard(app, updates)
        # invalidate the tray menu cache so the next menu
        self._post_save_tray_cleanup(app)
        return side_effect_status
