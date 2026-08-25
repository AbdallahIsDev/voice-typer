"""Tray state publication — extracted from ``tray.py``.

Owns the "publish the current tray state" concern:

  - :data:`_APP_STATE_TO_ICON_NAME` — AppState → logical icon-name map
    consumed by both the pystray redraw path and the Tauri
    ``tray_state`` event.
  - :func:`compute_tooltip` — the single tooltip formatter shared by
    ``apply_state`` + ``publish_tray_state`` so pystray + Tauri stay
    in sync.
  - :func:`publish_tray_state` — ADR-0020 §6.5: push icon+tooltip to
    Tauri, deduped on the full ``(icon_name, tooltip)`` tuple under
    ``tray._publish_lock``.
  - :func:`apply_state` — apply state to the live pystray icon
    (redraw cache-skip + WinError 1402 stale-handle workaround),
    serialized by ``tray._icon_lock``.

The ``TrayIcon`` class keeps one-line delegate methods for each of
these so ``monkeypatch.setattr(TrayIcon.X, ...)`` and bound-method
identity keep working unchanged.

Namespace note: ``_make_icon`` is resolved at CALL time through the
``voice_typer.server.tray`` module object because tests rebind
``tray_module._make_icon`` there; a top-level import would freeze the
pre-patch binding. The same applies to the pystray proxy, which stays
in ``tray.py``.

Logs go through the ``voice_typer.server.tray`` logger so log records
keep their pre-split attribution.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from voice_typer.server.branding import APP_NAME
from voice_typer.server.i18n import t as _i18n_t
from voice_typer.server.tray_types import AppState

if TYPE_CHECKING:
    from voice_typer.server.tray import TrayIcon

log = logging.getLogger("voice_typer.server.tray")

# ADR-0020 §6.5: maps internal AppState → logical icon name accepted by
# the Tauri Rust host's tray_state listener (whitelists
# "idle" | "recording" | "transcribing" | "error"). LOADING/CANCELLING
# fall back to a neighboring state (no dedicated asset).
_APP_STATE_TO_ICON_NAME: dict[AppState, str] = {
    AppState.IDLE: "idle",
    AppState.RECORDING: "recording",
    AppState.TRANSCRIBING: "transcribing",
    AppState.ERROR: "error",
    AppState.LOADING: "idle",
    AppState.CANCELLING: "error",
}


def compute_tooltip(tray: TrayIcon, state: AppState, message: str) -> str:
    """Compute the tray tooltip: ``<APP_NAME> — <msg|state> [(CPU fallback)]
    [(mm:ss)] [<model>] (<hotkey>)``. Shared by _apply_state +
    _publish_tray_state so pystray + Tauri stay in sync."""
    title = APP_NAME
    if message:
        title += f" — {message}"
    elif state != AppState.IDLE:
        # Localized AppState label (``state.recording`` etc.) so the
        # fallback suffix follows the renderer locale like the
        # per-call messages do — the renderer pushes localized values
        # for ``state.<value>`` via ``set_tray_locale``
        # (``trayLabelsForLocale`` maps them to ``trayState.*``).
        # English output is byte-identical (en registry value == the
        # raw enum value).
        title += f" — {_i18n_t('state.' + state.value)}"
    if tray._cpu_fallback_active:
        title += " (CPU fallback)"
    if state == AppState.RECORDING and tray._recording_started_at is not None:
        elapsed = time.monotonic() - tray._recording_started_at
        title += f" ({tray._format_elapsed(elapsed)})"
    # Model name suffix — only when the configured model is ACTUALLY
    # on disk. A stale ``model_size`` (selected before the model was
    # deleted / never downloaded) must not be advertised next to
    # "Loading model..." or an ERROR message — that misleads the user
    # into thinking the named model is being (or failed being) loaded.
    # The probe is TTL-cached (5s) and cheap (one stat).
    if tray._config:  # model name
        from voice_typer.server.tray_models import is_active_model_downloaded

        model = getattr(tray._config, "model_size", "")
        if model and is_active_model_downloaded(tray._config):
            title += f" [{model}]"
    hotkey = tray._display_hotkey()  # hotkey
    if hotkey:
        title += f" ({hotkey})"
    # Win32 ``NOTIFYICONDATAW.szTip`` has a 128-char limit (127 +
    # NUL) — truncate to 127 chars (with a trailing ``…`` if
    # truncated) so the OS layer doesn't silently cut the tooltip.
    # ``…`` is a single codepoint (U+2026), so ``title[:126] + "…"``
    # is exactly 127 chars. Deterministic for the same input, so
    # the ``_last_published`` dedup tuple stays stable.
    if len(title) > 127:
        title = title[:126] + "…"
    return title


def publish_tray_state(tray: TrayIcon) -> None:
    """ADR-0020 §6.5: push icon+tooltip to Tauri (emit tray_state event
    instead of mutating pystray Icon). No-op on Electron/pystray.
    Best-effort (hot path).

    Suppress redundant publishes — the cache key is the
    FULL ``(icon_name, tooltip)`` tuple (not just icon_name), so a
    tooltip-only change still emits. A failed publish is NOT
    cached, so the next call retries (no silent drop).

    Publish-dedup invariants (relocated from ``__init__``):
    ``tray._last_published`` caches the last successfully published
    tuple; ``stop()`` clears it so a restarted tray re-publishes its
    initial state. Only a SUCCESSFUL publish is cached — a failed one
    is NOT, so the next call retries. ``tray._publish_lock`` is a
    dedicated Lock (not ``_icon_lock`` / ``_menu_lock``) serializing
    ONLY the check-then-publish-then-cache sequence so two concurrent
    callers (the 1s elapsed-recording tick vs a state-change IPC)
    cannot both pass the cache check and both emit, without
    over-serializing against the icon-teardown or menu-rebuild paths.
    """
    from voice_typer.server.tray_menu import publish_tray_state as _publish_event

    icon_name = _APP_STATE_TO_ICON_NAME.get(tray._state, "idle")
    tooltip = compute_tooltip(tray, tray._state, tray._message)
    # ``_publish_lock`` serializes the check-then-publish-then-cache
    # sequence so two concurrent callers (the 1s elapsed-recording
    # tick vs a state-change IPC) cannot both pass the cache check
    # and both emit. Held ONLY across the tuple comparison + the
    # publish (NOT across ``compute_tooltip`` or the icon-name
    # lookup, which are pure and may run concurrently).
    with tray._publish_lock:
        # identical last-published state → skip the emit
        # entirely (redundant tray_state events cause the Tauri
        # host to re-run tray.set_icon / tray.set_tooltip, which on
        # Windows is a DestroyIcon / LoadIcon round-trip per call).
        if tray._last_published == (icon_name, tooltip):
            return
        try:
            ok = _publish_event(icon=icon_name, tooltip=tooltip)
        except Exception:
            log.debug(
                "[TRAY] publish_tray_state failed (state=%s)",
                tray._state.value if hasattr(tray._state, "value") else tray._state,
                exc_info=True,
            )
            # Do NOT cache a failed publish — the next call must
            # retry.
            return
        # Only cache a successful publish (best-effort
        # publish_tray_state returns False instead of raising on
        # the sidecar-disconnected path — a False return must NOT
        # suppress the next retry).
        if ok:
            tray._last_published = (icon_name, tooltip)


def apply_state(tray: TrayIcon, state: AppState, message: str) -> None:
    """Apply state to the live icon (safe from any thread).

     skip the ``_make_icon`` redraw when
    ``state == tray._last_applied_state`` — the icon PNG depends only
    on ``state``, not on the ``message`` / elapsed time. The 1s
    elapsed-recording tick re-enters here every second; the
    cache-skip avoids re-malloc'ing a fresh PIL image + pystray icon
    handle on every tick (and avoids tickling the WinError 1402
    stale-handle bug —  / ). The tooltip assignment is
    UNCONDITIONAL so the elapsed ``mm:ss`` stays live.

    The entire body is serialized by ``tray._icon_lock`` so
    that a concurrent ``stop()`` cannot tear down ``tray._icon``
    (``tray._icon.stop()`` then ``tray._icon = None``) between this
    function's ``if not tray._icon: return`` check and the subsequent
    ``tray._icon.icon = ...`` / ``tray._icon.title = ...`` writes.
    Without the lock, the gap was the documented WinError 1402
    trigger (writing to a torn-down Icon). The caller's
    ``if tray._icon:`` check (e.g. in ``set_state``) is racy on its
    own — the re-check inside the lock is the authoritative guard.
    ``_icon_lock`` is an RLock because this path may re-enter through
    ``compute_tooltip`` and any future callback path that re-enters
    the icon's setter.
    """
    # Call-time lookup: tests rebind ``tray_module._make_icon``;
    # resolving through the module object keeps the patch effective.
    from voice_typer.server import tray as _tray_mod

    with tray._icon_lock:
        if not tray._icon:
            return
        # only redraw the icon on a state CHANGE.
        if state != tray._last_applied_state:
            try:
                tray._icon.icon = _tray_mod._make_icon(state)
            except OSError as exc:
                # pystray Windows DestroyIcon stale-handle
                # bug (WinError 1402) during rapid icon updates — clear the
                # private _icon_handle so pystray re-creates it next call
                # (pystray pinned to >=0.19,<0.20 in pyproject.toml).
                #
                # if a future pystray release (0.20+) removes or
                # renames the private ``_icon_handle`` attribute, the
                # workaround becomes a silent no-op — the OSError is
                # still raised on every icon update but the workaround
                # can't fire, so WinError 1402 resurfaces for users with
                # no diagnostic surface. Log a WARNING in that case so
                # the silent workaround failure shows up in diagnostics
                # (the regression test
                # ``tests/test_pystray_icon_handle_regression.py`` guards
                # this exact attribute via ``hasattr(pystray.Icon,
                # "_icon_handle")``).
                if hasattr(tray._icon, "_icon_handle"):
                    tray._icon._icon_handle = None
                else:
                    log.warning(
                        "[TRAY] pystray.Icon no longer exposes the private "
                        "`_icon_handle` attribute — DestroyIcon workaround "
                        "disabled (OSError: %r). The tray will keep running "
                        "but rapid icon updates on Windows may hit WinError "
                        "1402. Replace the "
                        "private attribute access with a public "
                        "`reset_icon_handle()` API when upstream exposes "
                        "it, and bump pystray to the release that ships it.",
                        exc,
                    )
            tray._last_applied_state = state
        # Tooltip is UNCONDITIONAL — elapsed mm:ss must stay live on the
        # 1s recording tick even when the icon was skipped.
        tray._icon.title = compute_tooltip(tray, state, message)
