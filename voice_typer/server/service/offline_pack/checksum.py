"""Background pack checksum (§8.10, §8.16)."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from types import ModuleType

from .core import (
    load_offline_pack_manifest,
    offline_pack_dir_for_version,
    offline_pack_manifest_path,
    verify_offline_pack_or_skip,
)
from .events import _publish_event

log = logging.getLogger(__name__)


# ── Background checksum (§8.10, §8.16) ────────────────────────────────────


class BackgroundChecksum:
    """Run :func:`verify_offline_pack_or_skip` on a daemon thread (§8.10, §8.16).

    Launch-time path uses :func:`offline_pack_exists` (cheap, sync).
    Background checksum runs in the daemon thread; on completion it
    publishes ``offline_pack_verified`` (success) or ``offline_pack_corrupt`` (failure)
    via the event bus.
    """

    def __init__(
        self,
        version: str,
        *,
        event_bus: ModuleType | None = None,
        root: Path | None = None,
    ) -> None:
        self.version = version
        self.event_bus = event_bus
        self.root = root
        self._thread: threading.Thread | None = None
        self._done = threading.Event()
        self._result: bool | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._done.clear()
        self._thread = threading.Thread(target=self._run, name=f"pack-checksum-{self.version}", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            ok = verify_offline_pack_or_skip(self.version, root=self.root)
        except Exception:
            log.exception("[PACK] background checksum crashed for %s", self.version)
            ok = False
        self._result = ok
        self._done.set()
        if ok:
            # E9 parity: the renderer's OfflinePackVerifiedEvent requires
            # ``{version, sha256}``: same payload as the install-stage emit.
            manifest = load_offline_pack_manifest(offline_pack_manifest_path(self.version, root=self.root))
            _publish_event(
                self.event_bus,
                "offline_pack_verified",
                {"version": self.version, "sha256": manifest["sha256"] if manifest else ""},
            )
        else:
            _publish_event(
                self.event_bus,
                "offline_pack_corrupt",
                {
                    "version": self.version,
                    "path": str(offline_pack_dir_for_version(self.version, root=self.root)),
                    "reason": "background_checksum_failed",
                },
            )

    @property
    def done(self) -> bool:
        return self._done.is_set()

    @property
    def result(self) -> bool | None:
        """``True`` if verified, ``False`` if corrupt, ``None`` if still running."""
        return self._result

    def join(self, timeout_s: float | None = None) -> bool | None:
        if self._thread is not None:
            self._thread.join(timeout_s)
        return self._result
