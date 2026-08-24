"""Linux window-button layout detection (system integration).

The renderer's frameless title bar draws its own minimize/maximize/close
buttons. On Linux, desktop conventions vary per user: GNOME stores the
button layout (which buttons exist, on which side) in
``gsettings org.gnome.desktop.wm.preferences button-layout``, and KDE
sessions style their chrome differently from GNOME. This module is the
single source of truth for that system state:

* :func:`detect_desktop_environment` — classify the session
  (``"kde" | "gnome" | "xfce" | "mate" | "other" | "unknown"``) from the
  standard session env vars (``KDE_FULL_SESSION``, ``XDG_CURRENT_DESKTOP``).
* :func:`parse_button_layout` — parse a ``button-layout`` value like
  ``"appmenu:minimize,maximize,close"`` into ``{"side", "buttons"}``.
* :func:`system_window_buttons` — the cached snapshot exposed to the
  renderer through the read-only ``linux_window_buttons_system`` field of
  the ``get_config`` IPC response.

Design constraints:

* **Never blocks startup**: the ``gsettings`` subprocess is bounded by a
  2-second timeout and every failure degrades to ``None`` (the renderer
  then falls back to the built-in right-side minimize/maximize/close
  default).
* **Read-only**: this module never writes settings and never spawns
  anything except ``gsettings get``.
* **Cached once per process**: the session layout cannot change while
  the app runs (it is read at login), so one snapshot per process is
  correct and keeps ``get_config`` free of subprocess latency.
"""

from __future__ import annotations

import logging
import os
import platform
import subprocess
import threading

log = logging.getLogger(__name__)

_GSETTINGS_SCHEMA = "org.gnome.desktop.wm.preferences"
_GSETTINGS_KEY = "button-layout"
_GSETTINGS_TIMEOUT_SECONDS = 2

# The only caption buttons we model. Anything else in the layout value
# (``appmenu``, ``spacer``, ...) is ignored.
_KNOWN_BUTTONS = frozenset({"minimize", "maximize", "close"})

_CACHE: dict[str, object] | None = None
_CACHE_LOCK = threading.Lock()


def detect_desktop_environment(
    env: dict[str, str] | None = None,
) -> str:
    """Classify the running desktop session.

    Checks ``KDE_FULL_SESSION`` first (set by Plasma regardless of
    ``XDG_CURRENT_DESKTOP`` spelling), then the ``XDG_CURRENT_DESKTOP``
    colon-list. Returns one of ``"kde"``, ``"gnome"``, ``"xfce"``,
    ``"mate"``, ``"other"``, or ``"unknown"`` (no session markers — e.g.
    tests or a headless process).
    """
    environ = os.environ if env is None else env
    if environ.get("KDE_FULL_SESSION"):
        return "kde"
    xdg = (environ.get("XDG_CURRENT_DESKTOP") or "").lower()
    if not xdg:
        return "unknown"
    if "kde" in xdg or "plasma" in xdg:
        return "kde"
    if any(marker in xdg for marker in ("gnome", "unity", "ubuntu", "pantheon", "budgie", "cinnamon")):
        return "gnome"
    if "xfce" in xdg:
        return "xfce"
    if "mate" in xdg:
        return "mate"
    return "other"


def parse_button_layout(value: str | None) -> dict[str, object] | None:
    """Parse a GNOME ``button-layout`` value into ``{"side", "buttons"}``.

    The value format is ``"[<left-items>:]<right-items>"`` — exactly one
    side carries buttons, the other side of the colon holds menu tokens
    (``appmenu`` etc.). A colon-less value is treated as right-side.
    Tokens outside :data:`_KNOWN_BUTTONS` are dropped. Returns ``None``
    when no known button remains (e.g. ``":"`` = no caption buttons).
    """
    if not value:
        return None
    cleaned = value.strip().strip("'\"")
    if ":" in cleaned:
        left, _, right = cleaned.partition(":")
        left_buttons = [t for t in left.split(",") if t in _KNOWN_BUTTONS]
        right_buttons = [t for t in right.split(",") if t in _KNOWN_BUTTONS]
        if left_buttons:
            return {"side": "left", "buttons": left_buttons}
        if right_buttons:
            return {"side": "right", "buttons": right_buttons}
        return None
    buttons = [t for t in cleaned.split(",") if t in _KNOWN_BUTTONS]
    if not buttons:
        return None
    return {"side": "right", "buttons": buttons}


def _query_gsettings() -> str | None:
    """Read the raw ``button-layout`` value; ``None`` on any failure."""
    try:
        result = subprocess.run(  # noqa: S603 — fixed argv, no shell
            ["gsettings", "get", _GSETTINGS_SCHEMA, _GSETTINGS_KEY],
            capture_output=True,
            text=True,
            timeout=_GSETTINGS_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("[WINDOW_BUTTONS] gsettings probe failed: %s", exc)
        return None
    if result.returncode != 0:
        log.debug(
            "[WINDOW_BUTTONS] gsettings exit %s: %s",
            result.returncode,
            (result.stderr or "").strip(),
        )
        return None
    return (result.stdout or "").strip() or None


def system_window_buttons(
    env: dict[str, str] | None = None,
    *,
    force_refresh: bool = False,
) -> dict[str, object]:
    """Return the cached system snapshot ``{desktop_environment, layout}``.

    ``layout`` is :func:`parse_button_layout` output or ``None`` when the
    platform is not Linux or the ``gsettings`` probe failed. The snapshot
    is computed once per process (the session layout is fixed at login);
    tests can pass ``force_refresh=True`` or seed ``env``.
    """
    global _CACHE  # noqa: PLW0603 — single-process snapshot cache
    with _CACHE_LOCK:
        if _CACHE is not None and not force_refresh:
            return _CACHE
        desktop = detect_desktop_environment(env)
        layout: dict[str, object] | None = None
        if platform.system() == "Linux":
            raw = _query_gsettings()
            if raw is not None:
                layout = parse_button_layout(raw)
        _CACHE = {"desktop_environment": desktop, "layout": layout}
        return _CACHE


def reset_cache_for_tests() -> None:
    """Clear the module-level snapshot cache (test isolation only)."""
    global _CACHE  # noqa: PLW0603
    with _CACHE_LOCK:
        _CACHE = None


__all__ = [
    "detect_desktop_environment",
    "parse_button_layout",
    "reset_cache_for_tests",
    "system_window_buttons",
]
