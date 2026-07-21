"""Config side-effect dispatcher — extracted from ``service.py`` (CR-18).

ARCH-005 / CR-18: ``VoiceTyperService.apply_config_side_effects`` (215
LOC, 8 branching blocks, 12 distinct side-effects) and
``VoiceTyperService.apply_config`` (110 LOC: credential routing +
setattr + side-effects + save + tray-cache invalidation) previously
lived inline in ``service.py``.  This module owns that concern.

Public surface (preserved verbatim from ``VoiceTyperService`` so tests
+ IPC handlers don't notice the move):

- :meth:`ConfigApplier.apply_config_side_effects`
- :meth:`ConfigApplier.apply_config`

CR-61: the ``filters_dict`` DRY helper :func:`to_filter_dict` is the
single source of truth for the audio-filter settings dict pushed to
the level monitor + mic test on config changes.  Previously two
near-identical dicts lived in ``service.py`` with divergent defaults
(``noise_filter_rnnoise`` was False in one and True in the other).
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any

log = logging.getLogger(__name__)


# CR-61: canonical audio-filter dict keys.  ADR 0007 §5 lists 8 filter
# toggles; both call sites previously carried only 5 (missing
# ``noise_filter_eq``, ``noise_filter_compressor``,
# ``noise_filter_limiter``, ``noise_filter_notch``).  The level
# monitor / mic test path doesn't actually need those 4 (they only
# affect the dictation AudioProcessor, not the live level bar), so
# the canonical dict here mirrors the existing 5-key set — but with
# UNIFORM defaults (``noise_filter_rnnoise`` defaults to True per
# Config dataclass) so the two call sites don't drift.
_AUDIO_FILTER_KEYS = (
    "noise_filter_enabled",
    "noise_filter_highpass",
    "noise_filter_gate",
    "noise_filter_rnnoise",
    "noise_filter_post_capture",
)


def to_filter_dict(config: Any) -> dict:
    """CR-61: build the audio-filter settings dict from a Config.

    Single source of truth for the 5-key filter dict consumed by
    :func:`level_monitor.update_level_processor` and
    :func:`microphone_test.update_test_filters`.  Both call sites in
    ``service.py`` (``level_monitor_start`` and
    ``apply_config_side_effects``) now route through this helper so
    the defaults can't diverge (the previous two inline dicts
    disagreed on ``noise_filter_rnnoise``).

    Parameters
    ----------
    config : Config
        The application config dataclass.  ``getattr`` is used
        throughout so a partial / mock config (missing fields) still
        returns a complete dict — the defaults match the Config
        dataclass defaults.

    Returns
    -------
    dict
        A 5-key dict suitable for ``update_level_processor`` /
        ``update_test_filters``.
    """
    # Defaults mirror the Config dataclass declarations in config.py:
    # noise_filter_enabled=True, noise_filter_highpass=True,
    # noise_filter_gate=True, noise_filter_rnnoise=True (ADR 0007
    # changed the rnnoise default from False to True),
    # noise_filter_post_capture=True.
    return {
        "noise_filter_enabled": getattr(config, "noise_filter_enabled", True),
        "noise_filter_highpass": getattr(config, "noise_filter_highpass", True),
        "noise_filter_gate": getattr(config, "noise_filter_gate", True),
        "noise_filter_rnnoise": getattr(config, "noise_filter_rnnoise", True),
        "noise_filter_post_capture": getattr(config, "noise_filter_post_capture", True),
    }


def _apply_audio_preset(preset: str) -> dict:
    """ADR 0007: Map an audio preset name to individual filter settings.

    Delegates to :mod:`voice_typer.server.audio_presets` (single source
    of truth). Presets:
        "auto"        — all filters ON, RNNoise (best for 90% of users)
        "studio"      — minimal processing (quiet room, good mic)
        "noisy_room"  — aggressive, DeepFilterNet
        "off"         — all filters OFF
        "custom"      — no automatic changes (user controls each toggle)

    Legacy preset names "recommended" and "none" are accepted for
    backward compat (mapped to "auto" and "off" respectively).

    Returns:
        dict of noise_filter_* settings to apply.
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


class ConfigApplier:
    """Owns the post-config-update side-effect dispatch.

    Constructed once at service init with a reference to the parent
    service (so it can reach ``self._app.config``, ``self._app.tray``,
    and call back into ``service._invalidate_tray_models_cache`` /
    ``service._invalidate_model_status_cache``).
    """

    def __init__(self, service: Any) -> None:
        self._service = service
        self._app = service._app

    # ── Side-effects (CR-18 extraction / CR-65) ────────────────────

    def apply_config_side_effects(self, updates: dict) -> None:
        """Apply side effects after config changes.

        ARCH-005: Centralizes the post-config-update hooks that were
        previously scattered across ipc_server.py.

        CR-65: this method was a 215-line branching monolith in
        ``service.py``.  The extraction to ``config_applier.py`` is
        the first step; the branching structure itself is preserved
        verbatim so no behaviour change is introduced in this pass.
        A future refactor can replace the if-chain with a registered
        ``ConfigSideEffect`` protocol + handler list (CR-65 step 2).
        """
        app = self._app
        config = app.config

        # Sync autostart if autostart setting changed
        if "autostart" in updates:
            try:
                # RW-9 Phase 2: invoke startup_tasks directly. The
                # ``app._sync_autostart`` delegate was removed; callers now
                # target startup_tasks (and tests monkeypatch startup_tasks).
                from voice_typer.server import startup_tasks

                startup_tasks.sync_autostart(app)
            except Exception as e:
                log.warning("Failed to sync autostart: %s", e)

        # PW-3: Sync the prewarm scheduled task when fast_startup changes.
        # When the user toggles fast_startup in Settings → General, the
        # OS-level scheduled task must be registered (True) or
        # unregistered (False) immediately — otherwise the task fires
        # silently at next logon and exits with EXIT_DISABLED, or fails
        # to fire when the user re-enables it.
        if "fast_startup" in updates:
            try:
                from voice_typer.server import startup_tasks

                startup_tasks.sync_prewarm_task(app)
                log.info(
                    "[SERVICE] Prewarm task synced after fast_startup change (fast_startup=%s)",
                    bool(updates.get("fast_startup")),
                )
            except Exception as e:
                log.warning("Failed to sync prewarm task: %s", e)

        # Register/unregister ESC hotkey
        if "esc_cancel_enabled" in updates:
            try:
                if updates["esc_cancel_enabled"]:
                    app.hotkeys.register_esc()
                else:
                    app.hotkeys.unregister_esc()
            except Exception as e:
                log.warning("Failed to sync ESC hotkey: %s", e)

        # Register/unregister repaste hotkey
        if "repaste_hotkey" in updates or "repaste_enabled" in updates:
            try:
                app.hotkeys.register_repaste()
            except Exception as e:
                log.warning("Failed to sync repaste hotkey: %s", e)

        # NEW-UX-027: re-register the dictation hotkey when recording_mode
        # or hotkey changes.
        if "recording_mode" in updates or "hotkey" in updates or "push_to_talk_hotkey" in updates:
            try:
                app.hotkeys.restart(getattr(config, "hotkey", "<f2>"))
                log.info(
                    "[SERVICE] Re-registered hotkey after recording_mode/hotkey change (mode=%s)",
                    getattr(config, "recording_mode", "toggle"),
                )
            except Exception as e:
                log.warning("Failed to re-register hotkey after mode change: %s", e)

        # BUGFIX: tray_left_click_action was never handled — the tray
        # hardcoded "Toggle Dictation" as the left-click default, so the
        # Settings page choice was completely ignored.
        if "tray_left_click_action" in updates:
            try:
                app.tray.invalidate_menu_cache()
                log.info(
                    "[SERVICE] Tray left-click action updated to: %s",
                    updates["tray_left_click_action"],
                )
            except Exception as e:
                log.warning("Failed to update tray left-click action: %s", e)

        # BUGFIX: show_notifications changes were not applied until restart.
        if "show_notifications" in updates:
            try:
                app.tray.set_notifications_enabled(bool(updates["show_notifications"]))
                log.info(
                    "[SERVICE] Notifications %s",
                    "enabled" if updates["show_notifications"] else "disabled",
                )
            except Exception as e:
                log.warning("Failed to update notifications: %s", e)

        # BUGFIX: bubble_behavior changes were not applied until restart.
        if "bubble_behavior" in updates:
            try:
                behavior = updates["bubble_behavior"]
                if behavior == "always_visible":
                    try:
                        if hasattr(app, "_waveform_bubble"):
                            app._waveform_bubble.show()
                    except Exception:
                        pass
                elif behavior == "show_on_record":
                    # Hide bubble immediately when switching away from always_visible
                    try:
                        if hasattr(app, "_waveform_bubble") and app._waveform_bubble.visible:
                            app._waveform_bubble.hide()
                    except Exception:
                        pass
                log.info("[SERVICE] Bubble behavior updated to: %s", behavior)
            except Exception as e:
                log.warning("Failed to update bubble behavior: %s", e)

        # BUGFIX: volume_duck_smart changes were not applied until restart.
        if "volume_duck_smart" in updates:
            try:
                if hasattr(app, "_volume_ducker"):
                    app._volume_ducker.set_smart_duck_enabled(bool(updates["volume_duck_smart"]))
            except Exception as e:
                log.warning("Failed to update smart duck: %s", e)

        # BUGFIX: volume_duck_smart_poll_interval_ms changes not applied until restart.
        if "volume_duck_smart_poll_interval_ms" in updates:
            try:
                if hasattr(app, "_volume_ducker"):
                    app._volume_ducker.set_smart_duck_poll_interval(int(updates["volume_duck_smart_poll_interval_ms"]))
            except Exception as e:
                log.warning("Failed to update smart duck poll interval: %s", e)

        # Apply the audio enhancement preset: map preset name to filter toggles.
        if "audio_preset" in updates:
            try:
                preset = updates["audio_preset"]
                preset_filters = _apply_audio_preset(preset)
                # Set individual filter toggles from the preset
                for k, v in preset_filters.items():
                    setattr(config, k, v)
                # Sync the legacy noise_filter_enabled flag so downstream
                # checks (e.g. update_level_processor) correctly disable
                # the processor when preset is "off". The preset's filter
                # toggles are all False, but noise_filter_enabled was not
                # part of the preset dict — it stays True, causing the
                # level monitor to create an AudioProcessor even when no
                # filters are active, which masks low-level sounds.
                config.noise_filter_enabled = preset != "off"
                log.info("[SERVICE] Applied audio preset '%s': %s", preset, preset_filters)
            except Exception as e:
                log.warning("Failed to apply audio preset: %s", e)

        # ADR 0007 §6.1: Rebuild the dictation AudioProcessor's filter
        # chain when any noise_filter_* / audio_preset / noise_suppression_method
        # config field changes. This fixes the bug where Settings UI
        # changes didn't take effect in dictation until app restart.
        # All filter-chain-related config keys (old + new per ADR 0007 §5).
        filter_chain_keys = {
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
        if filter_chain_keys & set(updates.keys()):
            # ADR 0007: rebuild the dictation processor (the main fix).
            try:
                if hasattr(app, "_rebuild_audio_processor"):
                    app._rebuild_audio_processor()
            except Exception as e:
                log.warning("Failed to rebuild dictation audio processor: %s", e)

            # Also sync the live level bar + mic test processors so
            # they reflect the new filters immediately.
            try:
                from voice_typer.server.level_monitor import (
                    update_level_processor,
                    update_test_filters,
                )

                # CR-61: use the shared helper instead of an inline
                # 5-key dict (which diverged from the level-monitor-
                # start path on ``noise_filter_rnnoise``'s default).
                filters_dict = to_filter_dict(config)
                update_level_processor(filters_dict)
                update_test_filters(filters_dict)
            except Exception as e:
                log.warning("Failed to sync level bar processor: %s", e)

    # ── apply_config (CR-18 extraction) ────────────────────────────

    def apply_config(self, updates: dict) -> None:
        """Apply validated config updates atomically.

        ADR 0008 §3.1: wraps the config-mutation lock + setattr +
        side-effects + save + tray-cache invalidation sequence so the
        IPC ``set_config`` handler doesn't access
        ``self.app._config_mutation_lock``, ``self.app.config``, or
        ``self.app.tray.invalidate_menu_cache()`` directly.

        RACE-011: holds the app's config-mutation lock for the full
        read-modify-save sequence so a concurrent ``set_config`` IPC
        call can't interleave attribute writes with this update.

        AUDIO-PRESET-SAVE-FIX: runs :meth:`apply_config_side_effects`
        INSIDE the lock and saves AFTER it, so that any side-effect
        mutations (e.g. ``noise_filter_*`` toggles from the audio
        preset) are persisted to disk.  The previous order (save
        first, then apply side effects outside the lock) meant that
        when the user set ``audio_preset: "off"``, only the preset
        name was saved; the individual ``noise_filter_*`` toggles
        were NOT persisted.

        ARCH-043: invalidates the tray menu cache after the save so
        the next menu build picks up the new config values (model
        size, hotkey, etc.).

        RW-01: API key fields (``openai_api_key`` / ``groq_api_key`` /
        ``deepgram_api_key`` / ``cloud_api_key`` / ``llm_api_key``)
        are routed through ``credential_store.store_secret()`` BEFORE
        ``setattr(app.config, ...)`` so the secret lands in the OS
        keychain (with plaintext fallback). The in-memory Config
        attribute is then set to the real value so cloud_engines /
        llm_polish can use it. The subsequent ``app.config.save()``
        writes only a ``keyring://<provider>`` reference token to
        config.json (when keyring is available) — see
        ``Config.save()`` for the on-disk format.

        CR-97: calls ``app.config.save_strict()`` instead of
        ``app.config.save()``.  ``save_strict()`` raises
        ``RuntimeError`` if the underlying save returned ``False``
        (which indicates an ``OSError`` / ``PermissionError`` was
        caught and logged by ``save()``).  The IPC handler is
        expected to catch this and surface the failure to the
        renderer — previously a silent disk failure produced a
        successful-but-empty ``ack``.

        Parameters
        ----------
        updates :
            Validated config updates dict (allowlisted keys only).
            The caller is responsible for validating the payload —
            typically via :func:`voice_typer.server.config.validate_config_update`.
        """
        app = self._app
        with app._config_mutation_lock:
            # RW-01: pre-route api_key fields through credential_store.
            # We do this BEFORE setattr so that even if save() is
            # never called (e.g. apply_config_side_effects raises),
            # the secret is already persisted to the keychain. The
            # in-memory attribute is then set to the real value.
            try:
                from voice_typer.server import credential_store

                for k, v in list(updates.items()):
                    provider = credential_store.CONFIG_FIELD_TO_PROVIDER.get(k)
                    if provider is None:
                        continue
                    # store_secret never raises — it falls back to
                    # plaintext in config.json on keyring failure.
                    credential_store.store_secret(provider, v)
                    # The in-memory attribute carries the real value
                    # (NOT the keyring:// reference) so cloud_engines /
                    # llm_polish / dictation_pipeline can use it. The
                    # subsequent save() will replace the on-disk value
                    # with a reference token (when keyring is available).
            except Exception as exc:
                log.warning(
                    "[SERVICE] RW-01: credential_store pre-route failed: %s — "
                    "falling back to plain setattr (secret will be in config.json)",
                    exc,
                )
            for k, v in updates.items():
                setattr(app.config, k, v)
            # Drop the cached LLMPolisher when any llm_* config changes so the
            # next polish request rebuilds it with the new api_key/url/model/
            # preset. The polisher is constructed lazily in
            # DictationPipeline._apply_llm_polish from these fields; without
            # invalidation it would keep using stale credentials/settings.
            if any(k.startswith("llm_") for k in updates):
                with contextlib.suppress(Exception):
                    app._llm_polisher = None
            # Apply side effects inside the lock so Config mutations
            # from the preset are visible to save().
            self.apply_config_side_effects(updates)
            # CR-97: surface disk-write failures instead of silently
            # swallowing them.  ``save_strict`` raises RuntimeError
            # if ``save()`` returned False; the IPC handler is
            # expected to catch this and return an error envelope
            # instead of ``ack``.
            app.config.save_strict()

            # ADR-0010 §8.3b: propagate clipboard config changes to the
            # live ClipboardManager (DP7). Without this, runtime changes
            # to ``clipboard_save_restore`` / ``clipboard_restore_delay_ms``
            # / ``paste_on_stop`` would not take effect until app restart.
            # The keys are only present in ``updates`` because they passed
            # validation (see §2.11 — both keys are in
            # ``IPC_CONFIG_ALLOWLIST``). Run inside the lock so
            # ``refresh_config`` reads a consistent, persisted config
            # snapshot, not a torn one from a concurrent IPC update.
            clipboard_keys = {
                "clipboard_save_restore",
                "clipboard_restore_delay_ms",
                "paste_on_stop",
            }
            if clipboard_keys & set(updates.keys()):
                with contextlib.suppress(Exception):
                    app.clipboard.refresh_config(app.config)
        # ARCH-043: invalidate the tray menu cache so the next menu
        # build picks up the new config values.
        try:
            app.tray.invalidate_menu_cache()
        except Exception:
            log.debug("[SERVICE] tray.invalidate_menu_cache failed", exc_info=True)
