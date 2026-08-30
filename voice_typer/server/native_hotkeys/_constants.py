"""Native hotkey backend — shared constants.

Kept in a dedicated leaf module so mixin modules can import them
without circular dependencies.
"""

from __future__ import annotations

MAX_RESTART_ATTEMPTS = 5
RESTART_DELAY_BASE_SECONDS = 1.0  # 1, 2, 4, 8, 16s backoff
READY_TIMEOUT_SECONDS = 5.0

_WATCHDOG_PING_INTERVAL_SECONDS = 30.0
_WATCHDOG_PONG_TIMEOUT_SECONDS = 5.0
_WATCHDOG_RESPAWN_SECONDS = 60.0
