"""Crash recovery: stores last 10 transcriptions, checks on startup.

After each transcription, the text is saved to a recovery file.
On startup, if the recovery file has unpasted transcriptions,
the user is notified. The recovery file is cleared after acknowledgment.
"""

import json
import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

RECOVERY_FILENAME = "voice-typer-recovery.json"
MAX_RECOVERY_ENTRIES = 10


class CrashRecovery:
    """Stores recent transcriptions for crash recovery."""

    def __init__(self, config_dir: Optional[Path] = None):
        if config_dir is None:
            from voice_typer.server.config import _config_dir
            config_dir = _config_dir()
        self._path = config_dir / RECOVERY_FILENAME
        self._entries: list[dict] = []
        self._load()

    # ── Persistence ──────────────────────────────────────────────────

    def _load(self) -> None:
        """Load recovery entries from disk."""
        if not self._path.exists():
            self._entries = []
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                self._entries = data
            elif isinstance(data, dict) and "entries" in data:
                self._entries = data["entries"]
            else:
                self._entries = []
            log.debug("[RECOVERY] Loaded %d entries", len(self._entries))
        except Exception as exc:
            log.warning("[RECOVERY] Failed to load: %s", exc)
            self._entries = []

    def _save(self) -> None:
        """Save recovery entries to disk."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps({"entries": self._entries}, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            tmp.replace(self._path)
        except Exception as exc:
            log.error("[RECOVERY] Failed to save: %s", exc)

    # ── Public API ───────────────────────────────────────────────────

    def add(self, text: str, *, pasted: bool = False) -> None:
        """Add a transcription to the recovery buffer.

        Keeps only the last MAX_RECOVERY_ENTRIES entries.
        """
        from datetime import datetime
        entry = {
            "text": text,
            "timestamp": datetime.now().isoformat(),
            "pasted": pasted,
        }
        self._entries.append(entry)
        # Trim to max
        while len(self._entries) > MAX_RECOVERY_ENTRIES:
            self._entries.pop(0)
        self._save()

    def mark_pasted(self, index: int) -> bool:
        """Mark an entry as successfully pasted."""
        if 0 <= index < len(self._entries):
            self._entries[index]["pasted"] = True
            self._save()
            return True
        return False

    def mark_latest_pasted(self) -> None:
        """Mark the most recent entry as pasted."""
        if self._entries:
            self._entries[-1]["pasted"] = True
            self._save()

    def get_unpasted(self) -> list[dict]:
        """Return all entries that were not pasted (potential crash losses)."""
        return [e for e in self._entries if not e.get("pasted", False)]

    def get_all(self) -> list[dict]:
        """Return all recovery entries."""
        return list(self._entries)

    def check_on_startup(self) -> Optional[list[dict]]:
        """Check for unpasted transcriptions from a previous session.

        Returns a list of unpasted entries if any exist, or None.
        The caller should notify the user about these entries.
        """
        unpasted = self.get_unpasted()
        if unpasted:
            log.info("[RECOVERY] Found %d unpasted transcriptions from previous session", len(unpasted))
            return unpasted
        return None

    def clear(self) -> None:
        """Clear all recovery entries (after user acknowledgment)."""
        self._entries.clear()
        self._save()
        log.info("[RECOVERY] Recovery entries cleared")

    @property
    def count(self) -> int:
        """Number of recovery entries."""
        return len(self._entries)
