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
import json
import logging
from typing import Any

from voice_typer.server.branding import APP_NAME

log = logging.getLogger(__name__)


def _notify_side_effect_failure(app: Any, field: str, exc: BaseException) -> None:
    """PI-21: surface a config side-effect failure to the user via
    ``app.tray.notify`` so the user sees a toast instead of the failure
    being silently logged + swallowed. Mirrors the
    ``SettingsController.set_autostart`` pattern at
    ``settings_controller.py:107-108``.

    The config has ALREADY been mutated (via ``setattr`` in
    ``apply_config``) and WILL be persisted (via ``save_strict``) — so
    on-disk config says X while runtime state says Y. Without this
    notification the user has no signal that the runtime state didn't
    take effect (e.g. they enabled a filter, but the live audio
    processor wasn't rebuilt — the next dictation will use the OLD
    filter chain, and the user will wonder why their setting "didn't
    do anything").

    Parameters
    ----------
    app
        The VoiceTyperApp instance — must expose ``tray.notify(title,
        message)`` (the same API used by ``SettingsController`` and
        the dictation-pipeline ERROR path).
    field
        The config field name whose side-effect failed (e.g.
        ``"bubble_behavior"``, ``"audio_preset"``). Used in the toast
        message so the user can correlate the toast with the setting
        they just changed.
    exc
        The exception that triggered the failure. Logged at WARNING
        with ``exc_info=True`` by the caller; here we only need its
        ``str()`` for the toast message (truncated to keep the toast
        readable).
    """
    notify = getattr(getattr(app, "tray", None), "notify", None)
    if callable(notify):
        try:
            # Truncate the exception text so the toast stays readable
            # (some exception strings — e.g. ctranslate2 CUDA errors —
            # can run for hundreds of chars and wrap badly in a
            # 250px-wide toast).
            msg = str(exc)
            if len(msg) > 200:
                msg = msg[:197] + "..."
            notify(APP_NAME, f"Could not apply {field} change: {msg}")
        except Exception:
            # The notification itself failed — log at DEBUG (not
            # WARNING, to avoid a notification-failure loop) and
            # continue. The original side-effect failure was already
            # logged at WARNING by the caller.
            log.debug(
                "[CONFIG] tray.notify for side-effect failure also failed (field=%s)",
                field,
                exc_info=True,
            )
    else:
        # No tray.notify available (e.g. minimal test stub without a
        # real tray). Log at DEBUG so the missing-tray case is at
        # least visible in -vv mode.
        log.debug(
            "[CONFIG] app.tray.notify not available; cannot surface side-effect failure to user (field=%s)",
            field,
        )


def _json_dumps_sorted(obj: Any) -> str:
    """Stable JSON serialization for state comparison (G4-L-20 dirty-check).

    Serializes ``obj`` with ``sort_keys=True`` and ``default=str`` so
    that two semantically-equal dicts with different key orders compare
    equal. Used by :meth:`ConfigApplier.apply_config` to detect no-op
    updates (where the post-setattr Config state matches the
    pre-setattr state) and skip the ``save_strict()`` call.

    XV-120: retained for callers that introspect pre_state_dict for
    rollback logging; the dirty-check itself now compares only the
    ``updates`` keys via direct equality (no JSON serialization).
    """
    return json.dumps(obj, sort_keys=True, default=str)


# XV-124: hoisted from ``apply_config_side_effects``'s method body.
# Rebuilding a 30-element set literal on every IPC ``set_config`` call
# was pure waste — the keys never change at runtime. A module-level
# ``frozenset`` is built once at import and the ``&`` operator accepts
# a ``dict_keys`` view directly, so we can drop the ``set(...)`` wrapper
# on ``updates.keys()`` too.
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


def apply_config_side_effects(updates: dict, service: Any) -> dict:
    """XS-14 / CR-18 / Fix-D: module-level entry point for the
    post-config-update side-effect dispatch.

    This function exists primarily as the *delegation seam* referenced
    by ``tests/test_config_applier.py::test_service_apply_config_delegates_to_module``.
    The regression guard replaces this module attribute with a spy and
    asserts that ``VoiceTyperService.apply_config_side_effects`` invokes
    it — proving the service layer delegates to the extracted
    ``config_applier`` module rather than carrying the side-effect
    branching inline.

    The canonical dispatch logic lives in the
    :data:`voice_typer.server.service._CONFIG_SIDE_EFFECTS` registry
    (SVC-2 / CR-65 step 2) and is iterated directly by
    :meth:`VoiceTyperService.apply_config_side_effects`.  This
    module-level function does NOT iterate the registry itself — doing
    so would double-dispatch when called from
    :meth:`VoiceTyperService.apply_config_side_effects` (which then
    iterates the registry itself).  It returns an empty status dict
    (shape ``{"autostart_status": None, "prewarm_status": None}``) so
    the call is a no-op from the caller's perspective.

    External callers that want the full side-effect dispatch should
    call ``service.apply_config_side_effects(updates)`` directly (the
    instance method on :class:`VoiceTyperService`).

    Parameters
    ----------
    updates :
        Validated config updates dict (allowlisted keys only).
    service :
        The :class:`VoiceTyperService` instance whose app/config the
        side-effects would target (unused — kept in the signature so
        the spy in the regression guard receives the same positional
        args a real delegation would pass).

    Returns
    -------
    dict
        Empty status dict (no work performed).  The real status dict
        is returned by :meth:`VoiceTyperService.apply_config_side_effects`.
    """
    return {
        "autostart_status": None,
        "prewarm_status": None,
    }


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

    def apply_config_side_effects(self, updates: dict) -> dict:
        """Apply side effects after config changes.

        ARCH-005: Centralizes the post-config-update hooks that were
        previously scattered across ipc_server.py.

        CR-65: this method was a 215-line branching monolith in
        ``service.py``.  The extraction to ``config_applier.py`` is
        the first step; the branching structure itself is preserved
        verbatim so no behaviour change is introduced in this pass.
        A future refactor can replace the if-chain with a registered
        ``ConfigSideEffect`` protocol + handler list (CR-65 step 2).

        PVT-060 (session-3): returns a status dict so the caller
        (``apply_config`` → ``set_config`` IPC handler) can propagate
        autostart/prewarm registration results to the renderer. The
        dict shape is::

            {
                "autostart_status": {"registered": bool, "error": str | None} | None,
                "prewarm_status":   {"registered": bool, "error": str | None} | None,
            }

        A field is ``None`` when the corresponding config key wasn't in
        ``updates`` (no sync was attempted). The renderer reads
        ``autostart_status.error`` to surface "Autostart registration
        failed: <reason>" instead of silently failing.
        """
        app = self._app
        config = app.config

        # PVT-060: accumulate side-effect statuses for the renderer.
        # Each entry is None (no sync attempted) or a dict with
        # ``registered`` + ``error`` keys.
        side_effect_status: dict[str, dict | None] = {
            "autostart_status": None,
            "prewarm_status": None,
        }

        # Sync autostart if autostart setting changed
        if "autostart" in updates:
            try:
                # RW-9 Phase 2: invoke startup_tasks directly. The
                # ``app._sync_autostart`` delegate was removed; callers now
                # target startup_tasks (and tests monkeypatch startup_tasks).
                from voice_typer.server import startup_tasks

                side_effect_status["autostart_status"] = startup_tasks.sync_autostart(app)
            except Exception as e:
                log.warning("Failed to sync autostart: %s", e)
                side_effect_status["autostart_status"] = {"registered": False, "error": str(e)}
                # PI-21: surface the side-effect failure to the user via
                # a tray notification (the config has already been
                # mutated + persisted; the runtime state didn't take
                # effect, so the user needs a signal).
                _notify_side_effect_failure(app, "autostart", e)

        # PW-3: Sync the prewarm scheduled task when fast_startup changes.
        # When the user toggles fast_startup in Settings → General, the
        # OS-level scheduled task must be registered (True) or
        # unregistered (False) immediately — otherwise the task fires
        # silently at next logon and exits with EXIT_DISABLED, or fails
        # to fire when the user re-enables it.
        if "fast_startup" in updates:
            try:
                from voice_typer.server import startup_tasks

                side_effect_status["prewarm_status"] = startup_tasks.sync_prewarm_task(app)
                log.info(
                    "[SERVICE] Prewarm task synced after fast_startup change (fast_startup=%s)",
                    bool(updates.get("fast_startup")),
                )
            except Exception as e:
                log.warning("Failed to sync prewarm task: %s", e)
                side_effect_status["prewarm_status"] = {"registered": False, "error": str(e)}
                # PI-21: surface the prewarm task sync failure to the
                # user via a tray notification.
                _notify_side_effect_failure(app, "fast_startup", e)

        # Register/unregister ESC hotkey
        if "esc_cancel_enabled" in updates:
            try:
                if updates["esc_cancel_enabled"]:
                    app.hotkeys.register_esc()
                else:
                    app.hotkeys.unregister_esc()
            except Exception as e:
                log.warning("Failed to sync ESC hotkey: %s", e)
                # PI-21: surface the ESC hotkey sync failure to the
                # user via a tray notification.
                _notify_side_effect_failure(app, "esc_cancel_enabled", e)

        # Register/unregister repaste hotkey
        if "repaste_hotkey" in updates or "repaste_enabled" in updates:
            try:
                app.hotkeys.register_repaste()
            except Exception as e:
                log.warning("Failed to sync repaste hotkey: %s", e)
                # PI-21: surface the repaste hotkey sync failure to
                # the user via a tray notification.
                _notify_side_effect_failure(app, "repaste_hotkey", e)

        # NEW-UX-027: re-register the dictation hotkey when recording_mode
        # or hotkey changes.
        # FR-21: dropped the push_to_talk_hotkey disjunct (the third
        # ``or <field> in updates`` clause that lived here pre-fix) —
        # ``push_to_talk_hotkey`` was deliberately removed from
        # ``IPC_CONFIG_ALLOWLIST`` per GT-F2-8, so it can never appear
        # in ``updates`` via the IPC path. The disjunct was dead code
        # that misled reviewers into thinking the branch handled a
        # user-tunable setting. If ``push_to_talk_hotkey`` is ever
        # re-wired, the allowlist AND this side-effect branch must be
        # added together.
        if "recording_mode" in updates or "hotkey" in updates:
            # G4-H-17: snapshot the previous hotkey so we can restore it
            # if ``app.hotkeys.restart()`` raises. ``restart()`` sets
            # ``config.hotkey = <new>`` before calling ``register()`` —
            # if ``register()`` then fails (or restart itself raises),
            # the on-disk config retains the broken hotkey. We restore
            # the previous value and re-save so the next launch reads a
            # working hotkey.
            #
            # (Agent 2-k owns the parallel fix inside
            # ``hotkey_dispatcher.restart()`` itself — restore the
            # config.hotkey value when ``register()`` returns False.
            # This block covers the case where ``restart()`` raises.)
            old_hotkey = getattr(config, "hotkey", None)
            try:
                app.hotkeys.restart(getattr(config, "hotkey", "<f2>"))
                log.info(
                    "[SERVICE] Re-registered hotkey after recording_mode/hotkey change (mode=%s)",
                    getattr(config, "recording_mode", "toggle"),
                )
            except Exception as e:
                log.warning("Failed to re-register hotkey after mode change: %s", e)
                # G4-H-17: restore previous hotkey + re-save so a
                # failed restart doesn't leave the on-disk config with
                # a broken hotkey value.
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
                # PI-21: surface the tray left-click action update
                # failure to the user via a tray notification.
                _notify_side_effect_failure(app, "tray_left_click_action", e)

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
                # PI-21: surface the notifications update failure to
                # the user via a tray notification.
                _notify_side_effect_failure(app, "show_notifications", e)

        # BUGFIX: bubble_behavior changes were not applied until restart.
        if "bubble_behavior" in updates:
            try:
                behavior = updates["bubble_behavior"]
                if behavior == "always_visible":
                    try:
                        if hasattr(app, "_waveform_bubble"):
                            app._waveform_bubble.show()
                    except Exception:
                        # PVT-G5-047 (session-5): previously `except Exception: pass`
                        # — silent failure meant "always visible" toggle
                        # did nothing if the bubble was in a bad state.
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
                        # PVT-G5-047: same as above — log at debug so the
                        # failure is at least visible in -vv mode.
                        log.debug(
                            "[SERVICE] Failed to hide waveform bubble after bubble_behavior change",
                            exc_info=True,
                        )
                log.info("[SERVICE] Bubble behavior updated to: %s", behavior)
            except Exception as e:
                log.warning("Failed to update bubble behavior: %s", e)
                # PI-21: surface the bubble behavior update failure to
                # the user via a tray notification.
                _notify_side_effect_failure(app, "bubble_behavior", e)

        # FR-21: the ``volume_duck_smart`` side-effect branch (an
        # ``if <field> in updates:`` block that lived here at lines
        # 518-545 in the pre-fix source) was DEAD CODE — the
        # ``volume_duck_smart`` field was removed from the Config
        # dataclass (UX-2/GT-58) and from ``IPC_CONFIG_ALLOWLIST``,
        # so the condition could never be True via the IPC path. The
        # branch survived the field removal because the deletion was
        # missed. We delete it outright (rather than leaving a comment
        # + dead body) so code review reflects reality: smart duck is
        # ALWAYS ON when ``volume_duck_enabled`` is True, and the only
        # user-tunable volume-ducking controls are ``volume_duck_enabled``
        # / ``volume_duck_level`` / ``volume_duck_fade_ms`` /
        # ``volume_duck_smart_poll_interval_ms``. If
        # ``volume_duck_smart`` is ever re-added to the dataclass AND
        # the allowlist, the side-effect branch must be re-added here
        # alongside them — the three changes go together.

        # BUGFIX: volume_duck_smart_poll_interval_ms changes not applied until restart.
        if "volume_duck_smart_poll_interval_ms" in updates:
            try:
                if hasattr(app, "_volume_ducker"):
                    app._volume_ducker.set_smart_duck_poll_interval(int(updates["volume_duck_smart_poll_interval_ms"]))
            except Exception as e:
                log.warning("Failed to update smart duck poll interval: %s", e)
                # PI-21: surface the smart duck poll interval update
                # failure to the user via a tray notification.
                _notify_side_effect_failure(app, "volume_duck_smart_poll_interval_ms", e)

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
                # PI-21: surface the audio preset apply failure to the
                # user via a tray notification.
                _notify_side_effect_failure(app, "audio_preset", e)

        # ADR 0007 §6.1: Rebuild the dictation AudioProcessor's filter
        # chain when any noise_filter_* / audio_preset / noise_suppression_method
        # config field changes. This fixes the bug where Settings UI
        # changes didn't take effect in dictation until app restart.
        # XV-124: ``_FILTER_CHAIN_KEYS`` is a module-level frozenset; the
        # ``&`` operator accepts the ``updates.keys()`` view directly so
        # we no longer wrap it in ``set(...)`` (which would allocate a
        # fresh set on every IPC call).
        if _FILTER_CHAIN_KEYS & updates.keys():
            # ADR 0007: rebuild the dictation processor (the main fix).
            try:
                if hasattr(app, "_rebuild_audio_processor"):
                    app._rebuild_audio_processor()
            except Exception as e:
                log.warning("Failed to rebuild dictation audio processor: %s", e)
                # PI-21: surface the audio-processor rebuild failure
                # to the user via a tray notification. This is the
                # most user-visible failure mode: the user changed a
                # noise_filter_* toggle but the live dictation pipeline
                # is still using the OLD filter chain — the next
                # dictation will sound wrong.
                _notify_side_effect_failure(app, "noise_filter_chain", e)

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
                # PI-21: surface the level bar processor sync failure
                # to the user via a tray notification.
                _notify_side_effect_failure(app, "level_bar_filters", e)

        # PVT-060 (session-3): return the accumulated side-effect
        # statuses so :meth:`apply_config` can propagate them to the
        # ``set_config`` IPC response. The renderer reads
        # ``autostart_status.error`` / ``prewarm_status.error`` to
        # surface registration failures.
        return side_effect_status

    # ── apply_config (CR-18 extraction) ────────────────────────────

    def apply_config(self, updates: dict) -> dict:
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

        Returns
        -------
        dict
            PVT-060 (session-3): side-effect status dict with the
            shape ``{"autostart_status": dict | None, "prewarm_status": dict | None}``.
            The ``set_config`` IPC handler propagates this to the
            renderer so it can surface "Autostart registration failed:
            <reason>" instead of silently failing. A field is ``None``
            when the corresponding config key wasn't in ``updates``.
        """
        app = self._app
        # PVT-060 (session-3): capture the side-effect status dict for
        # return. The ``with`` block below may raise (e.g. ``save_strict``
        # raises RuntimeError on disk-write failure) — in that case we
        # still want to return whatever side-effect status was captured
        # before the raise, so the renderer can surface the autostart/
        # prewarm status alongside the save error. Initialize to all-
        # None so the return shape is stable even on early-raise.
        side_effect_status: dict[str, dict | None] = {
            "autostart_status": None,
            "prewarm_status": None,
        }
        # G4-L-20 + G4-H-12: snapshot pre-setattr Config state. Used for
        # both the dirty-check (skip ``save_strict()`` if state is
        # unchanged — G4-L-20) and for rollback on ``save_strict()``
        # failure (restore snapshot + re-run side-effects with original
        # values so live state matches disk — G4-H-12).
        from dataclasses import asdict as _asdict

        try:
            pre_state_dict = _asdict(app.config)
        except Exception:
            # Snapshot failed (e.g. Config is a MagicMock in tests).
            # Skip the dirty-check and rollback paths — they require a
            # real dataclass instance to introspect.
            pre_state_dict = None

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
            # G4-L-24: wrap the setattr loop in try/except. On exception,
            # restore pre-loop values for the keys we already set, then
            # re-raise so the caller sees the original error.
            _MISSING = object()  # noqa: N806
            set_keys: list[tuple[str, Any]] = []
            try:
                for k, v in updates.items():
                    old_value = getattr(app.config, k, _MISSING)
                    set_keys.append((k, old_value))
                    setattr(app.config, k, v)
            except Exception:
                # Restore pre-loop values for keys we already set, in
                # reverse order so a partial setattr chain doesn't
                # compound the corruption.
                for k, old_value in reversed(set_keys):
                    try:
                        if old_value is not _MISSING:
                            setattr(app.config, k, old_value)
                    except Exception:
                        log.warning(
                            "[SERVICE] G4-L-24: failed to restore config key %s during setattr rollback",
                            k,
                            exc_info=True,
                        )
                raise
            # Drop the cached LLMPolisher when any llm_* config changes so the
            # next polish request rebuilds it with the new api_key/url/model/
            # preset. The polisher is constructed lazily in
            # DictationPipeline._apply_llm_polish from these fields; without
            # invalidation it would keep using stale credentials/settings.
            if any(k.startswith("llm_") for k in updates):
                with contextlib.suppress(Exception):
                    app._llm_polisher = None
            # Apply side effects inside the lock so Config mutations
            # from the preset are visible to save(). PVT-060 (session-3):
            # capture the returned status dict for propagation to the IPC
            # response.
            side_effect_status = self.apply_config_side_effects(updates)
            # CR-97: surface disk-write failures instead of silently
            # swallowing them.  ``save_strict`` raises RuntimeError
            # if ``save()`` returned False; the IPC handler is
            # expected to catch this and return an error envelope
            # instead of ``ack``.
            #
            # G4-L-20: dirty-check — if the post-setattr state equals
            # the pre-setattr state (e.g. the user submitted an empty
            # update or all values were already the same), skip the
            # save_strict() call entirely. This avoids an unnecessary
            # disk write + atomic-rename dance for no-op updates.
            #
            # XV-120: previously this dirty-check did
            # ``_json_dumps_sorted(pre_state_dict) == _json_dumps_sorted(post_state_dict)``
            # which serialised the FULL Config (150+ fields) twice via
            # ``dataclasses.asdict`` (deep-copy) and twice via
            # ``json.dumps`` per IPC ``set_config`` call. The
            # targeted check below compares only the ``updates`` keys
            # via direct equality — O(len(updates)) instead of
            # O(len(Config fields)). It reuses the pre-setattr values
            # already captured in ``set_keys`` (G4-L-24 rollback log)
            # so no extra getattr pass is needed before setattr.
            # ``pre_state_dict`` is still captured (above) for the
            # G4-H-12 rollback path — it's NOT used by the dirty-check
            # any more.
            post_values = {k: getattr(app.config, k, _MISSING) for k in updates}
            pre_values = dict(set_keys)
            state_unchanged = pre_state_dict is not None and pre_values == post_values
            if state_unchanged:
                log.debug("[SERVICE] G4-L-20: apply_config detected no state change — skipping save_strict()")
            else:
                try:
                    app.config.save_strict()
                except Exception:
                    # G4-H-12: save_strict failed (disk write error,
                    # permission denied, etc.). The in-memory Config now
                    # carries the new values while disk holds the old.
                    # Restore the snapshot under the same lock so the
                    # in-memory state matches disk again, then re-run
                    # apply_config_side_effects with the ORIGINAL values
                    # so live side-effects (hotkey registration, audio
                    # filter rebuild, etc.) match the restored config.
                    if pre_state_dict is not None:
                        for k, v in pre_state_dict.items():
                            try:
                                setattr(app.config, k, v)
                            except Exception:
                                log.warning(
                                    "[SERVICE] G4-H-12: failed to restore config key %s during save_strict rollback",
                                    k,
                                    exc_info=True,
                                )
                        # Build an "old updates" dict (only the keys
                        # the caller asked to change) so the side-effects
                        # re-run with the values that are now live.
                        old_updates = {k: v for k, v in pre_state_dict.items() if k in updates}
                        if old_updates:
                            try:
                                self.apply_config_side_effects(old_updates)
                            except Exception:
                                log.warning(
                                    "[SERVICE] G4-H-12: failed to re-run side-effects during save_strict rollback",
                                    exc_info=True,
                                )
                    raise

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
                # PVT-G5-047 (session-5): previously
                # ``contextlib.suppress(Exception)`` — silent failure
                # meant runtime changes to clipboard_save_restore /
                # clipboard_restore_delay_ms / paste_on_stop silently
                # did not apply until restart. ADR-0010 §8.3b
                # specifically calls out that refresh is needed for
                # runtime changes; suppressing defeated the purpose.
                # Log at WARNING so the operator knows to restart for
                # the config change to take effect.
                try:
                    app.clipboard.refresh_config(app.config)
                except Exception as exc:
                    log.warning(
                        "[SERVICE] clipboard.refresh_config failed: %s — "
                        "clipboard config changes will not take effect until restart",
                        exc,
                    )
        # ARCH-043: invalidate the tray menu cache so the next menu
        # build picks up the new config values.
        try:
            app.tray.invalidate_menu_cache()
        except Exception:
            log.debug("[SERVICE] tray.invalidate_menu_cache failed", exc_info=True)
        return side_effect_status
