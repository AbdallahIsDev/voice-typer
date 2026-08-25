"""Config side-effect dispatcher — extracted from ``service.py`` ().

``VoiceTyperService.apply_config_side_effects`` (215
LOC, 8 branching blocks, 12 distinct side-effects) and
``VoiceTyperService.apply_config`` (110 LOC: credential routing +
setattr + side-effects + save + tray-cache invalidation) previously
lived inline in ``service.py``.  This module owns that concern.

Public surface (preserved verbatim from ``VoiceTyperService`` so tests
+ IPC handlers don't notice the move):

- :meth:`ConfigApplier.apply_config_side_effects`
- :meth:`ConfigApplier.apply_config`

the ``filters_dict`` DRY helper :func:`to_filter_dict` is the
single source of truth for the audio-filter settings dict pushed to
the level monitor + mic test on config changes.  Previously two
near-identical dicts lived in ``service.py`` with divergent defaults
(``noise_filter_rnnoise`` was False in one and True in the other).
"""

from __future__ import annotations

import contextlib
import json
import logging
from dataclasses import dataclass
from typing import Any, Protocol

from voice_typer.server.branding import APP_NAME

# import ``DEFAULT_HOTKEY`` so the ``hotkeys.restart()`` fallback
# uses the canonical default (``<caps_lock>``) instead of the stale
# literal ``"<f2>"`` that lived here pre-fix. ``<f2>`` was the legacy
# default before  centralised the default-hotkey constant; the
# fallback should never fire in practice (Config always carries a
# ``hotkey`` field) but if it does, it must agree with the platform
# default the rest of the codebase uses.
from voice_typer.server.config import DEFAULT_HOTKEY

log = logging.getLogger(__name__)


def _notify_side_effect_failure(app: Any, field: str, exc: BaseException) -> None:
    """surface a config side-effect failure to the user via
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
    """Stable JSON serialization for state comparison ( dirty-check).

        Serializes ``obj`` with ``sort_keys=True`` and ``default=str`` so
        that two semantically-equal dicts with different key orders compare
        equal. Used by :meth:`ConfigApplier.apply_config` to detect no-op
        updates (where the post-setattr Config state matches the
        pre-setattr state) and skip the ``save_strict()`` call.

    retained for callers that introspect pre_state_dict for
        rollback logging; the dirty-check itself now compares only the
        ``updates`` keys via direct equality (no JSON serialization).
    """
    return json.dumps(obj, sort_keys=True, default=str)


# hoisted from ``apply_config_side_effects``'s method body.
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


# canonical audio-filter dict keys.  ADR 0007 §5 lists 8 filter
# toggles; both call sites previously carried only 5 (missing
# ``noise_filter_eq``, ``noise_filter_compressor``,
# ``noise_filter_limiter``, ``noise_filter_notch``).  The level
# monitor / mic test path doesn't actually need those 4 (they only
# affect the dictation AudioProcessor, not the live level bar), so
# the canonical dict here mirrors the existing 5-key set — but with
# UNIFORM defaults (``noise_filter_rnnoise`` defaults to True per
# Config dataclass) so the two call sites don't drift.

# XZ-CFG-10: the set of config keys that ``apply_preset`` overwrites
# when ``audio_preset != "custom"`` (see ``audio_presets.PRESETS``).
# If a user submits an IPC ``set_config`` for any of these keys while
# ``audio_preset`` is one of the named presets (auto / studio /
# noisy_room / off), the next ``Config.load()`` will call
# ``apply_preset`` again and silently revert the user's toggle to the
# preset's value. ``apply_config`` detects this case and auto-switches
# ``audio_preset`` to ``"custom"`` (with an INFO log) so the user's
# individual toggle survives a restart. The set mirrors the keys in
# ``audio_presets.PRESETS`` exactly — kept here as a frozenset (rather
# than dynamically derived from ``PRESETS``) so the value is bound at
# import time and the auto-switch check is O(1).
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


def to_filter_dict(config: Any) -> dict:
    """build the audio-filter settings dict from a Config.

    Single source of truth for the filter dict consumed by
    :func:`level_monitor.update_level_processor` and
    :func:`microphone_test.update_test_filters`.  Both call sites in
    ``service.py`` (``level_monitor_start`` and
    ``apply_config_side_effects``) now route through this helper so
    the defaults can't diverge (the previous two inline dicts
    disagreed on ``noise_filter_rnnoise``).

    The dict is COMPLETE: every ``noise_filter_*`` / ``noise_suppression_*``
    field (plus ``audio_preset``) declared on the ``Config`` dataclass is
    included. The earlier 5-key version omitted ``noise_filter_notch``
    (and the eq/compressor/limiter/gate-* fields), which crashed
    ``AudioProcessor`` construction with ``'SimpleNamespace' object has
    no attribute 'noise_filter_notch'`` — and the partial dict was also
    stashed as ``_state._level_processor_config``, breaking every later
    level-processor rebuild after a device hot-swap.

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
        A complete filter dict suitable for ``update_level_processor`` /
        ``update_test_filters``.
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


def _apply_audio_preset(preset: str) -> dict:
    """ADR 0007: Map an audio preset name to individual filter settings.

    Delegates to :mod:`voice_typer.server.audio_presets` (single source
    of truth). Presets:
        "auto"        — all filters ON, RNNoise (best for 90% of users)
        "studio"      — minimal processing (quiet room, good mic)
        "noisy_room"  — aggressive, GTCRN
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
    """Fix-D: module-level entry point for the
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
    (SVC-2 /  step 2) and is iterated directly by
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


# ─── Registered side-effect handlers ─────────────────────────────────
#
# The ``ConfigApplier.apply_config_side_effects`` method used to be a
# 215-line if-chain — one ``if "X" in updates:`` block per config
# field that needed a runtime side-effect. Each block followed the
# same pattern: try → run side-effect → except → log warning +
# ``_notify_side_effect_failure``. The if-chain has been replaced
# with a registered ``ConfigSideEffect`` protocol + handler list
# (the docstring on ``apply_config_side_effects`` below documents the
# motivation). Each handler is a small, focused class with an
# ``applies(updates)`` predicate (the old ``if "X" in updates:``
# check) and an ``apply(ctx)`` method (the old block body). Handlers
# are stateless and share a single instance each; they are registered
# in :attr:`ConfigApplier._side_effect_handlers` and iterated in
# registration order. Order matters: the audio-preset handler
# mutates Config (sets ``noise_filter_*`` toggles from the preset),
# and the filter-chain handler reads that mutated Config via
# ``to_filter_dict(config)`` — so audio_preset MUST run before
# filter_chain. The original if-chain had this order implicitly; the
# registered handler list makes it explicit.


@dataclass
class SideEffectContext:
    """Context passed to each registered :class:`ConfigSideEffect` handler.

    Bundles the inputs every handler needs (``app``, ``config``,
    ``updates``, ``status``) so the dispatcher can iterate handlers
    with a single context object rather than passing four arguments
    to each ``apply()`` call. Handlers mutate ``status`` in place
    (only the autostart + prewarm handlers do — they set
    ``status["autostart_status"]`` / ``status["prewarm_status"]`` to
    the result dict returned by ``startup_tasks.sync_*``).
    """

    app: Any
    config: Any
    updates: dict
    status: dict[str, dict[str, Any] | None]


class ConfigSideEffect(Protocol):
    """Protocol for a registered config side-effect handler.

    Each handler decides whether it applies to the current ``updates``
    dict (via :meth:`applies`) and, if so, runs the side-effect (via
    :meth:`apply`). Handlers are registered in
    :attr:`ConfigApplier._side_effect_handlers` and iterated in order
    by :meth:`ConfigApplier.apply_config_side_effects`.

    A handler's ``apply`` method is expected to catch its own
    exceptions (preserving the original log-and-continue behaviour of
    the if-chain this refactor replaced) — the dispatcher wraps each
    handler in a defensive try/except as well, so a buggy handler
    cannot bring down the entire dispatch.
    """

    #: Short identifier used in log messages + tray notifications.
    #: Matches the config-field name the original if-block used (e.g.
    #: ``"autostart"``, ``"hotkey"``, ``"audio_preset"``) so users see
    #: the same toast text as before the refactor.
    name: str

    def applies(self, updates: dict) -> bool:
        """Return True if this handler should run for the given updates."""
        ...

    def apply(self, ctx: SideEffectContext) -> None:
        """Apply the side-effect.

        Should log + notify via :func:`_notify_side_effect_failure` on
        failure rather than raising — the dispatcher's outer try/except
        is a defensive net, not the primary error path.
        """
        ...


class _AutostartSyncHandler:
    """Sync OS autostart entry when ``autostart`` config changes."""

    name = "autostart"

    def applies(self, updates: dict) -> bool:
        return "autostart" in updates

    def apply(self, ctx: SideEffectContext) -> None:
        app = ctx.app
        try:
            # Phase 2: invoke startup_tasks directly. The
            # ``app._sync_autostart`` delegate was removed; callers now
            # target startup_tasks (and tests monkeypatch startup_tasks).
            from voice_typer.server import startup_tasks

            ctx.status["autostart_status"] = startup_tasks.sync_autostart(app)
        except Exception as e:
            log.warning("Failed to sync autostart: %s", e)
            ctx.status["autostart_status"] = {"registered": False, "error": str(e)}
            # surface the side-effect failure to the user via
            # a tray notification (the config has already been
            # mutated + persisted; the runtime state didn't take
            # effect, so the user needs a signal).
            _notify_side_effect_failure(app, "autostart", e)


class _PrewarmSyncHandler:
    """Sync the prewarm scheduled task when ``fast_startup`` changes.

    When the user toggles fast_startup in Settings → General, the
    OS-level scheduled task must be registered (True) or unregistered
    (False) immediately — otherwise the task fires silently at next
    logon and exits with EXIT_DISABLED, or fails to fire when the user
    re-enables it.
    """

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
            # user via a tray notification.
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
            # user via a tray notification.
            _notify_side_effect_failure(app, "esc_cancel_enabled", e)


class _RepasteHotkeyHandler:
    """Re-register repaste hotkey when ``repaste_hotkey`` changes.

    ``repaste_enabled`` is a run-time toggle on the repaste *action*
    (whether the repaste hotkey, when pressed, actually fires the
    repaste) — it does NOT change the hotkey registration. The
    disjunct ``or "repaste_enabled" in updates`` that lived in the
    original if-block was dead code: ``register_repaste()`` reads
    ``config.repaste_hotkey`` (the actual hotkey spec) so the call
    was harmless, but it was wasted work and misled reviewers into
    thinking ``repaste_enabled`` affected registration. Only
    ``repaste_hotkey`` triggers a re-register.
    """

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
            # the user via a tray notification.
            _notify_side_effect_failure(app, "repaste_hotkey", e)


class _DictationHotkeyHandler:
    """Re-register dictation hotkey when ``recording_mode`` or ``hotkey`` changes.

    Snapshots the previous hotkey so we can restore it if
    ``app.hotkeys.restart()`` raises. ``restart()`` sets
    ``config.hotkey = <new>`` before calling ``register()`` — if
    ``register()`` then fails (or restart itself raises), the on-disk
    config retains the broken hotkey. We restore the previous value
    and re-save so the next launch reads a working hotkey.
    """

    name = "hotkey"

    def applies(self, updates: dict) -> bool:
        return "recording_mode" in updates or "hotkey" in updates

    def apply(self, ctx: SideEffectContext) -> None:
        app = ctx.app
        config = ctx.config
        # snapshot the previous hotkey so we can restore it
        # if ``app.hotkeys.restart()`` raises. ``restart()`` sets
        # ``config.hotkey = <new>`` before calling ``register()`` —
        # if ``register()`` then fails (or restart itself raises),
        # the on-disk config retains the broken hotkey. We restore
        # the previous value and re-save so the next launch reads a
        # working hotkey.
        old_hotkey = getattr(config, "hotkey", None)
        try:
            # use ``DEFAULT_HOTKEY`` (the canonical platform default
            # from ``config.py``, currently ``<caps_lock>``) as the
            # fallback instead of the stale literal ``"<f2>"``.
            # ``<f2>`` was the legacy default before the constant was
            # centralised — leaving it here meant a hypothetical
            # config object without a ``hotkey`` attribute (test
            # stub / legacy Config constructed via ``__new__``) would
            # silently re-register the wrong key. In practice Config
            # always carries ``hotkey``, so the fallback is defensive
            # — but it must agree with the rest of the codebase when
            # it does fire.
            app.hotkeys.restart(getattr(config, "hotkey", DEFAULT_HOTKEY))
            log.info(
                "[SERVICE] Re-registered hotkey after recording_mode/hotkey change (mode=%s)",
                getattr(config, "recording_mode", "toggle"),
            )
        except Exception as e:
            log.warning("Failed to re-register hotkey after mode change: %s", e)
            # restore previous hotkey + re-save so a
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


class _TrayLeftClickHandler:
    """Invalidate tray menu cache when ``tray_left_click_action`` changes.

    BUGFIX: tray_left_click_action was never handled — the tray
    hardcoded "Toggle Dictation" as the left-click default, so the
    Settings page choice was completely ignored.
    """

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
            # failure to the user via a tray notification.
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
            # the user via a tray notification.
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
                    # same as above — log at debug so the
                    # failure is at least visible in -vv mode.
                    log.debug(
                        "[SERVICE] Failed to hide waveform bubble after bubble_behavior change",
                        exc_info=True,
                    )
            log.info("[SERVICE] Bubble behavior updated to: %s", behavior)
        except Exception as e:
            log.warning("Failed to update bubble behavior: %s", e)
            # surface the bubble behavior update failure to
            # the user via a tray notification.
            _notify_side_effect_failure(app, "bubble_behavior", e)


class _VolumeDuckPollHandler:
    """Update smart-duck poll interval when ``volume_duck_smart_poll_interval_ms`` changes.

    BUGFIX: volume_duck_smart_poll_interval_ms changes were not applied
    until restart.

    Note: the legacy ``volume_duck_smart`` side-effect branch (a
    ``volume_duck_smart``-in-updates guard in the pre-refactor source)
    was DEAD CODE — the ``volume_duck_smart`` field was removed from the
    Config dataclass and from ``IPC_CONFIG_ALLOWLIST``, so the condition
    could never be True via the IPC path. Smart duck is ALWAYS ON when
    ``volume_duck_enabled`` is True, and the only user-tunable
    volume-ducking controls are ``volume_duck_enabled`` /
    ``volume_duck_level`` / ``volume_duck_fade_ms`` /
    ``volume_duck_smart_poll_interval_ms``. If ``volume_duck_smart`` is
    ever re-added to the dataclass AND the allowlist, a corresponding
    handler must be re-added here alongside them — the three changes
    go together.
    """

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
            # failure to the user via a tray notification.
            _notify_side_effect_failure(app, "volume_duck_smart_poll_interval_ms", e)


class _AudioPresetHandler:
    """Apply audio preset (map preset name → filter toggles) when ``audio_preset`` changes.

    Syncs the legacy ``noise_filter_enabled`` flag so downstream checks
    (e.g. ``update_level_processor``) correctly disable the processor
    when preset is "off". The preset's filter toggles are all False,
    but ``noise_filter_enabled`` was not part of the preset dict — it
    stays True, causing the level monitor to create an AudioProcessor
    even when no filters are active, which masks low-level sounds.
    """

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
            # surface the audio preset apply failure to the
            # user via a tray notification.
            _notify_side_effect_failure(app, "audio_preset", e)


class _FilterChainHandler:
    """Rebuild dictation AudioProcessor + sync level monitor when any filter-chain key changes.

    ADR 0007 §6.1: rebuild the dictation processor when any
    ``noise_filter_*`` / ``audio_preset`` / ``noise_suppression_method``
    config field changes. This fixes the bug where Settings UI changes
    didn't take effect in dictation until app restart.
    ``_FILTER_CHAIN_KEYS`` is a module-level frozenset; the ``&``
    operator accepts the ``updates.keys()`` view directly so we don't
    allocate a fresh set on every IPC call.
    """

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

            # use the shared helper instead of an inline
            # 5-key dict (which diverged from the level-monitor-
            # start path on ``noise_filter_rnnoise``'s default).
            filters_dict = to_filter_dict(config)
            update_level_processor(filters_dict)
            update_test_filters(filters_dict)
        except Exception as e:
            log.warning("Failed to sync level bar processor: %s", e)
            # surface the level bar processor sync failure
            # to the user via a tray notification.
            _notify_side_effect_failure(app, "level_bar_filters", e)


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
        # Build the handler list at construction time. Each handler is
        # stateless — it reads its inputs from the
        # :class:`SideEffectContext` — so a single shared instance per
        # handler is sufficient. Order matters: the audio-preset
        # handler mutates Config (sets ``noise_filter_*`` toggles from
        # the preset), and the filter-chain handler reads that mutated
        # Config via ``to_filter_dict(config)`` — so audio_preset MUST
        # run before filter_chain. The original if-chain had this order
        # implicitly; the registered handler list makes it explicit.
        # The list is a per-instance attribute (not a class attribute)
        # so tests can monkeypatch it on a single ConfigApplier instance
        # without affecting other instances.
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

    # Side-effects ( extraction / refactor) ────────────────────

    def apply_config_side_effects(self, updates: dict) -> dict:
        """Apply side effects after config changes.

        Centralizes the post-config-update hooks that were previously
        scattered across ``ipc_server.py``.

        Previously a 215-line branching monolith in ``service.py``
        (one ``if "X" in updates:`` block per config field that needed
        a runtime side-effect). The extraction to ``config_applier.py``
        was the first step; the branching structure was preserved
        verbatim through that pass. The if-chain has now been replaced
        with a registered :class:`ConfigSideEffect` protocol + handler
        list — each ``if "X" in updates:`` block from the original
        monolith is now an ``applies(updates)`` + ``apply(ctx)``
        method pair on a dedicated handler class, registered in
        :attr:`_side_effect_handlers` and iterated in registration
        order by this method. Behaviour is preserved verbatim: each
        handler carries the same try/except + log + notify pattern as
        the original block, and the dispatcher's outer try/except is a
        defensive net for handler bugs (``applies()`` raising, etc.)
        that the original if-chain didn't need because each block was
        inlined.

        Returns
        -------
        dict
            Side-effect status dict with the shape::

                {
                    "autostart_status": {"registered": bool, "error": str | None} | None,
                    "prewarm_status":   {"registered": bool, "error": str | None} | None,
                }

            A field is ``None`` when the corresponding config key wasn't
            in ``updates`` (no sync was attempted). The renderer reads
            ``autostart_status.error`` to surface "Autostart registration
            failed: <reason>" instead of silently failing.
        """
        app = self._app
        config = app.config

        # accumulate side-effect statuses for the renderer.
        # Each entry is None (no sync attempted) or a dict with
        # ``registered`` + ``error`` keys.
        side_effect_status: dict[str, dict | None] = {
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
                # exceptions internally (preserving the original
                # log-and-continue behaviour of the if-chain this
                # refactor replaced), but a bug in ``applies()`` or an
                # unexpected raise should not bring down the entire
                # dispatch. Log + notify with the handler's ``name``
                # (which matches the config-field name the original
                # block used) so the user sees the same toast.
                handler_name = getattr(handler, "name", type(handler).__name__)
                log.warning(
                    "[SERVICE] Side-effect handler %s raised unexpectedly: %s",
                    handler_name,
                    e,
                    exc_info=True,
                )
                _notify_side_effect_failure(app, handler_name, e)

        # return the accumulated side-effect statuses so
        # :meth:`apply_config` can propagate them to the ``set_config``
        # IPC response. The renderer reads ``autostart_status.error`` /
        # ``prewarm_status.error`` to surface registration failures.
        return side_effect_status

    # apply_config ( extraction) ────────────────────────────

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

        Invalidates the tray menu cache after the save so the next
        menu build picks up the new config values (model size,
        hotkey, etc.).

        API key fields (``openai_api_key`` / ``groq_api_key`` /
        ``deepgram_api_key`` / ``cloud_api_key`` / ``llm_api_key``)
        are routed through ``credential_store.store_secret()`` AFTER
        ``app.config.save_strict()`` succeeds. Previously the routing
        happened BEFORE ``setattr(app.config, ...)``; on a
        ``save_strict`` disk-write failure the in-memory Config was
        rolled back to the OLD value via ``set_keys`` but the
        keychain retained the NEW value, leaving the keychain
        inconsistent with disk + in-memory state. Deferring to AFTER
        ``save_strict`` keeps the keychain in lock-step with disk: if
        save fails, the keychain is NOT touched (it still holds
        whatever a prior successful save wrote). The in-memory Config
        attribute carries the real value (NOT the ``keyring://``
        reference) so cloud_engines / llm_polish / dictation_pipeline
        can use it; the subsequent ``app.config.save()`` (called
        inside ``save_strict``) writes only a ``keyring://<provider>``
        reference token to config.json (when keyring is available) —
        see ``Config.save()`` for the on-disk format.

        Calls ``app.config.save_strict()`` instead of
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
            Side-effect status dict with the shape
            ``{"autostart_status": dict | None, "prewarm_status": dict | None}``.
            The ``set_config`` IPC handler propagates this to the
            renderer so it can surface "Autostart registration failed:
            <reason>" instead of silently failing. A field is ``None``
            when the corresponding config key wasn't in ``updates``.
        """
        # SEC-002 defense-in-depth: even though the IPC
        # ``set_config`` handler runs ``validate_config_update`` (which
        # silently drops non-allowlisted keys) BEFORE calling
        # ``service.apply_config``, this check at the boundary of
        # ``apply_config`` itself surfaces any internal caller (e.g. a
        # new IPC handler that forgets to invoke
        # ``validate_config_update``) that tries to ``setattr`` a
        # non-allowlisted field onto ``app.config``. Without this, a
        # bug in any caller would silently let a trusted-path field
        # (e.g. ``schema_version``, ``qwen_model_path``) be mutated at
        # runtime, defeating SEC-002.
        #
        # Implementation note (minimal fix): we log CRITICAL and
        # CONTINUE (no ``raise``) rather than raising ``ValueError``
        # because some existing internal callers (e.g.
        # ``tests/test_config_acl_and_preset_autoswitch.py``) invoke
        # ``apply_config`` directly with deprecated Config-runtime
        # fields (``noise_filter_enabled`` — a runtime switch per ADR
        # 0009 that was removed from ``IPC_CONFIG_ALLOWLIST`` but is
        # still on the Config dataclass) to exercise the preset
        # auto-switch logic. Raising would break those tests
        # (NEVER DOWNGRADE — Rule 4). The CRITICAL log surfaces the
        # violation observably so operators can grep for it; a future
        # cleanup can tighten this to a hard ``raise`` once the
        # deprecated runtime-only fields are removed from the Config
        # dataclass or the test fixtures are updated to use
        # allowlisted substitutes.
        from voice_typer.server.config_validators import IPC_CONFIG_ALLOWLIST

        _unknown = set(updates) - IPC_CONFIG_ALLOWLIST.keys()
        if _unknown:
            log.critical(
                "[SERVICE] SEC-002 violation: apply_config received "
                "non-allowlisted keys %s — the IPC ``set_config`` handler "
                "should have dropped these via validate_config_update. "
                "Continuing (no raise) for backward compat with internal "
                "callers that pass deprecated runtime-only Config fields "
                "(e.g. noise_filter_enabled). Investigate the caller if "
                "this appears in production logs.",
                sorted(_unknown),
            )
        app = self._app
        # (session-3): capture the side-effect status dict for
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
        # + : snapshot pre-setattr Config state. Used for
        # both the dirty-check (skip ``save_strict()`` if state is
        # unchanged — ) and for rollback on ``save_strict()``
        # failure (restore snapshot + re-run side-effects with original
        # values so live state matches disk — ).
        with app._config_mutation_lock:
            # XZ-CFG-10: if the user submits an individual noise_filter_*
            # toggle (one of the keys ``apply_preset`` overwrites) while
            # ``audio_preset`` is a named preset (auto / studio /
            # noisy_room / off), auto-switch ``audio_preset`` to
            # ``"custom"`` BEFORE setattr. Without this, ``Config.load()``
            # would call ``apply_preset`` on next restart and silently
            # revert the user's toggle to the preset's value (e.g. user
            # sets ``noise_filter_highpass=False`` while preset is
            # ``"auto"``, restarts, ``apply_preset("auto", instance)``
            # sets it back to ``True``).
            #
            # Skip when the user explicitly set ``audio_preset`` in
            # this same update — they're picking a preset, so the
            # preset's toggles are the intent. Also skip when the
            # preset is already ``"custom"`` (no-op).
            if "audio_preset" not in updates:
                individual_overrides = _PRESET_OVERRIDE_KEYS & updates.keys()
                if individual_overrides:
                    current_preset = getattr(app.config, "audio_preset", "custom")
                    if current_preset != "custom":
                        log.info(
                            "[CONFIG] individual filter toggles %s set via "
                            "IPC while audio_preset=%r — auto-switching "
                            "audio_preset to 'custom' so the user's toggle "
                            "survives the next Config.load() (which would "
                            "otherwise re-apply the preset and revert it)",
                            sorted(individual_overrides),
                            current_preset,
                        )
                        updates = {**updates, "audio_preset": "custom"}
            # wrap the setattr loop in try/except. On exception,
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
            # from the preset are visible to save().  (session-3):
            # capture the returned status dict for propagation to the IPC
            # response.
            side_effect_status = self.apply_config_side_effects(updates)
            # surface disk-write failures instead of silently
            # swallowing them.  ``save_strict`` raises RuntimeError
            # if ``save()`` returned False; the IPC handler is
            # expected to catch this and return an error envelope
            # instead of ``ack``.
            #
            # dirty-check — if the post-setattr state equals
            # the pre-setattr state (e.g. the user submitted an empty
            # update or all values were already the same), skip the
            # save_strict() call entirely. This avoids an unnecessary
            # disk write + atomic-rename dance for no-op updates.
            #
            # previously this dirty-check did
            # ``_json_dumps_sorted(pre_state_dict) == _json_dumps_sorted(post_state_dict)``
            # which serialised the FULL Config (150+ fields) twice via
            # ``dataclasses.asdict`` (deep-copy) and twice via
            # ``json.dumps`` per IPC ``set_config`` call. The
            # targeted check below compares only the ``updates`` keys
            # via direct equality — O(len(updates)) instead of
            # O(len(Config fields)). It reuses the pre-setattr values
            # already captured in ``set_keys`` ( rollback log)
            # so no extra getattr pass is needed before setattr.
            # the eager ``dataclasses.asdict()`` snapshot
            # (``pre_state_dict``) has been removed entirely. The
            # dirty-check uses only ``set_keys``, and the
            # rollback path also uses ``set_keys`` to restore only the
            # mutated keys instead of the full 150+ Config snapshot.
            post_values = {k: getattr(app.config, k, _MISSING) for k in updates}
            pre_values = dict(set_keys)
            state_unchanged = pre_values == post_values
            if state_unchanged:
                log.debug("[SERVICE] G4-L-20: apply_config detected no state change — skipping save_strict()")
            else:
                try:
                    app.config.save_strict()
                except Exception:
                    # save_strict failed (disk write error,
                    # permission denied, etc.). The in-memory Config now
                    # carries the new values while disk holds the old.
                    # Restore the snapshot under the same lock so the
                    # in-memory state matches disk again, then re-run
                    # apply_config_side_effects with the ORIGINAL values
                    # so live side-effects (hotkey registration, audio
                    # filter rebuild, etc.) match the restored config.
                    # uses ``set_keys`` (the per-key pre-setattr
                    # value log) instead of an eager ``dataclasses.asdict()``
                    # snapshot of the full Config (150+ fields).
                    for k, old_value in set_keys:
                        try:
                            setattr(app.config, k, old_value)
                        except Exception:
                            log.warning(
                                "[SERVICE] G4-H-12: failed to restore config key %s during save_strict rollback",
                                k,
                                exc_info=True,
                            )
                    # Build an "old updates" dict (only the keys
                    # the caller asked to change) so the side-effects
                    # re-run with the values that are now live.
                    old_updates = dict(set_keys)
                    if old_updates:
                        try:
                            self.apply_config_side_effects(old_updates)
                        except Exception:
                            log.warning(
                                "[SERVICE] G4-H-12: failed to re-run side-effects during save_strict rollback",
                                exc_info=True,
                            )
                    raise
                # Defer credential_store.store_secret to AFTER
                # save_strict succeeded. Previously this block ran
                # BEFORE setattr, so on save_strict failure the
                # in-memory Config was rolled back to the OLD value via
                # ``set_keys`` while the keychain retained the NEW value,
                # leaving the keychain inconsistent with disk +
                # in-memory state. Now: if save_strict raises, the
                # ``raise`` above propagates BEFORE this block executes,
                # so the keychain is left untouched (it still holds
                # whatever a prior successful save wrote). If save_strict
                # succeeds, the keychain is updated to match the new
                # in-memory + on-disk state. ``store_secret`` never
                # raises (it falls back to plaintext in config.json on
                # keyring failure), so a broken D-Bus / locked Keychain
                # cannot break the save path here. Note: ``save_strict``
                # already routed the secret via ``Config.save()`` when
                # keyring is available, so this call is a redundant
                # safety net for the no-keyring-available plaintext
                # fallback path and for callers whose ``Config.save()``
                # was patched to skip routing (e.g. test mocks).
                #
                # Gate the redundant loop behind
                # ``app.config._secrets_routed_in_save`` (set True by
                # ``Config._save_unlocked`` after it runs the routing
                # block). When the flag is True (or missing — the
                # ``getattr`` default of True is the safe assumption for
                # Config instances from before this change), the loop is
                # SKIPPED because ``Config.save()`` already routed the
                # secret. The loop only runs when the flag is explicitly
                # False — i.e. ``Config.save()`` was mocked to skip
                # routing (test scenario) or the routing block raised
                # an exception (logged at WARNING inside
                # ``_save_unlocked``). This eliminates the redundant
                # ``store_secret`` call (and its lock re-acquisition
                # dance) on every successful ``apply_config`` IPC call.
                if not getattr(app.config, "_secrets_routed_in_save", True):
                    try:
                        from voice_typer.server import credential_store

                        for k, v in list(updates.items()):
                            provider = credential_store.CONFIG_FIELD_TO_PROVIDER.get(k)
                            if provider is None:
                                continue
                            credential_store.store_secret(provider, v)
                    except Exception as exc:
                        log.warning(
                            "[SERVICE] RW-01: credential_store post-save route "
                            "failed: %s — secret may not be in keychain (will "
                            "fall back to plaintext in config.json on next save)",
                            exc,
                        )

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
                # (session-5): previously
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
        # invalidate the tray menu cache so the next menu
        # build picks up the new config values.
        try:
            app.tray.invalidate_menu_cache()
        except Exception:
            log.debug("[SERVICE] tray.invalidate_menu_cache failed", exc_info=True)
        return side_effect_status
