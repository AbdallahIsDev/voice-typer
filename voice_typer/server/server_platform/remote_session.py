"""RDP / SSH remote-session detection + non-microphone device predicate.

Phase 4.5 / ARCH-045 — extracted from the original
``voice_typer/server/server_platform.py`` god-module.  The two helpers in
this file have no cross-submodule state: they only read ``SYSTEM`` (the
package-level ``sys.platform`` snapshot) and stdlib ``os`` / ``ctypes``.

Patch-path compatibility
------------------------
Tests do not directly patch ``is_remote_session`` via
``monkeypatch.setattr("voice_typer.server.server_platform.is_remote_session", ...)``
— instead, callers (e.g. ``clipboard.py``) import the function lazily
inside a try/except and tests replace the whole module via
``patch.dict(sys.modules, {"voice_typer.server.server_platform": fake})``.
For the dispatch on the platform, ``is_remote_session`` reads
``_pkg.SYSTEM`` (NOT a local ``SYSTEM`` binding) so a future test that
patches ``server_platform.SYSTEM`` would still take effect.

``inspect.getsource`` compatibility
-----------------------------------
``is_remote_session`` and ``_is_non_mic_device`` are genuinely defined
here, so ``inspect.getsource(is_remote_session)`` continues to read from
this file.
"""

from __future__ import annotations

import logging
import os

# Patch-path bridge: route lookups of ``SYSTEM`` through the package
# namespace so test patches of the form
# ``monkeypatch.setattr("voice_typer.server.server_platform.SYSTEM", "win32")``
# keep affecting production code defined here.  The package ``__init__.py``
# re-exports ``SYSTEM`` (it is a module-level constant of the package
# itself); we look it up at call time rather than binding at import time
# so the patch takes effect.
from voice_typer.server import server_platform as _pkg

log = logging.getLogger(__name__)


# ─── RDP / remote session detection ──────────────────────────────────


def is_remote_session() -> bool:
    """PLAT-RDP: Detect if the app is running in an RDP/remote session.

    On Windows, uses GetSystemMetrics(SM_REMOTESESSION = 0x1000).
    On Linux, checks $SSH_CLIENT or $SSH_TTY.
    RDP clipboard may be redirected, so clipboard operations may behave
    differently (e.g. clipboard sync delays, missing formats).

    Returns True if a remote session is detected.
    """
    if _pkg.SYSTEM == "win32":
        try:
            import ctypes

            # SM_REMOTESESSION = 0x1000
            result = ctypes.windll.user32.GetSystemMetrics(0x1000)
            if result:
                log.info("[PLATFORM] RDP/remote session detected (SM_REMOTESESSION=%d)", result)
                return True
        except Exception:
            log.debug("[PLATFORM] SM_REMOTESESSION probe failed", exc_info=True)
        return False
    else:
        # Linux/macOS: check for SSH session
        if os.environ.get("SSH_CLIENT") or os.environ.get("SSH_TTY"):
            log.info("[PLATFORM] SSH session detected (SSH_CLIENT/SSH_TTY set)")
            return True
        return False


# ─── Non-microphone device predicate ─────────────────────────────────


def _is_non_mic_device(name: str) -> bool:
    """Return True if the device name matches a known non-microphone input pattern."""
    lower = name.lower().strip()

    # Loopback / what-u-hear devices (captures speaker output, useless for voice)
    if any(p in lower for p in ["stereo mix", "what u hear", "wave out mix", "mono mix"]):
        return True

    # Physical line input jacks (silent unless something is plugged in)
    if any(p in lower for p in ["line in", "line input"]):
        return True

    # Auxiliary input
    if lower in ("aux", "auxiliary") or lower.startswith("aux ") or lower.startswith("auxiliary "):
        return True

    # System virtual devices that just mirror the default device
    # (redundant with "System Default" menu option)
    return bool(any(p in lower for p in ["microsoft sound mapper", "primary sound capture driver"]))
