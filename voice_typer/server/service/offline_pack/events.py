"""IPC event types + best-effort publish for the runtime pack family."""

from __future__ import annotations

import logging
from types import ModuleType

log = logging.getLogger(__name__)

# ── IPC events (§7.4, published via event_bus.publish) ──────────────────

OFFLINE_PACK_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "offline_pack_download_started",
        "offline_pack_download_progress",
        "offline_pack_download_completed",
        "offline_pack_download_failed",
        "offline_pack_verified",
        "offline_pack_missing",
        "offline_pack_corrupt",
        "offline_pack_ready",
        "worker_started",
        "worker_crashed",
        "worker_unloaded",
        "transcribe_offline",
        "transcribe_offline_result",
    }
)


# ── Event publishing (small wrapper for tests) ───────────────────────────


def _publish_event(event_bus: ModuleType | None, event_type: str, payload: dict) -> None:
    """Best-effort publish, swallow errors (the bus is best-effort)."""
    if event_bus is None:
        return
    try:
        event_bus.publish({"type": event_type, "data": payload})
    except Exception:
        log.debug("[PACK] event_bus publish failed for %s", event_type, exc_info=True)

