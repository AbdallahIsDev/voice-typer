"""Voice template manager: CRUD, match/expand, variable substitution.

Templates are trigger-phrase → output-text pairs stored in a JSON file.
When the user says a trigger phrase during dictation, the system replaces
the transcribed text with the stored output.

Pipeline order: transcribe → text cleanup → vocabulary → template match → auto-punctuate → paste

Variables supported in output text:
    {today}     — current date (e.g., "2026-06-03")
    {now}       — current time (e.g., "14:30")
    {clipboard} — current clipboard content
    {username}  — system username
"""

import getpass
import json
import logging
import re
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

TEMPLATES_FILENAME = "voice-typer-templates.json"

# G4-M-38: SEC-011-style caps for templates to prevent resource
# exhaustion. Mirror vocabulary.MAX_CORRECTIONS_ENTRIES pattern.
MAX_TEMPLATES = 1000
MAX_TRIGGER_LENGTH = 200
MAX_OUTPUT_LENGTH = 2000

# ─── Variable substitution ─────────────────────────────────────────────


def _get_clipboard_text() -> str:
    """Try to read current clipboard content."""
    try:
        import pyperclip

        text = pyperclip.paste()
        return str(text) if text and isinstance(text, str) else ""
    except Exception:
        return ""


def substitute_variables(text: str) -> str:
    """Replace template variables with their current values.

    Supported variables:
        {today}     — date in YYYY-MM-DD
        {now}       — time in HH:MM
        {clipboard} — current clipboard content
        {username}  — OS username
    """
    replacements = {
        "today": datetime.now().strftime("%Y-%m-%d"),
        "now": datetime.now().strftime("%H:%M"),
        "clipboard": _get_clipboard_text(),
        "username": _safe_getuser(),
    }
    for var, value in replacements.items():
        text = text.replace("{" + var + "}", value)
    return text


def _safe_getuser() -> str:
    """Get username safely, returning 'user' on any failure.

    NEW-CQ-013: ``getpass.getuser()`` always returns ``str`` (or raises).
    The previous ``isinstance(name, str)`` check was dead code. Simplified
    to a direct truthiness check.
    """
    try:
        name = getpass.getuser()
        return name if name else "user"
    except Exception:
        return "user"


# ─── Template manager ──────────────────────────────────────────────────


class TemplateManager:
    """Manages voice templates: CRUD, persistence, matching."""

    def __init__(self, config_dir: Path | None = None):
        if config_dir is None:
            from voice_typer.server.config import _config_dir

            config_dir = _config_dir()
        self._path = config_dir / TEMPLATES_FILENAME
        self._templates: list[dict] = []
        self._load()

    @property
    def templates(self) -> list[dict]:
        """Public read accessor for the templates list.

        XS-15: the previous public ``templates`` attribute was renamed
        to ``_templates`` (private) without a property shim, breaking
        every caller — tests, IPC handlers, the tray menu builder, and
        the on-disk persistence round-trip in
        ``tests/test_history_and_models.py::TestTemplatesPersistToDisk``.

        Returns a SHALLOW COPY of the underlying list so callers can
        iterate / index / clear the returned list without mutating the
        manager's internal state (per the
        ``test_templates_property_returns_copy`` contract: modifying the
        returned object must not affect the manager).

        The individual template dicts inside the list are NOT copied
        (shallow copy) — callers that need to mutate a template should
        use :meth:`update` so the change persists to disk.
        """
        return list(self._templates)

    # ── Persistence ──────────────────────────────────────────────────

    def _load(self) -> None:
        """Load templates from JSON file.

        SEC-audit-006 (Round 0 forward-port): uses
        :func:`voice_typer.server.config._secure_read_text`
        (POSIX ``O_NOFOLLOW`` + inode re-verification) to prevent a
        symlink-TOCTOU attack where an attacker replaces the templates
        file with a symlink to a sensitive file (e.g.
        ``~/.ssh/id_rsa``).  Previously this used
        :meth:`pathlib.Path.read_text`, which silently followed
        symlinks — inconsistent with :meth:`_save`, which already used
        :func:`_secure_atomic_write` (the write-side counterpart).
        If ``_secure_read_text`` raises (symlink detected, inode
        changed, or any other OSError/ValueError), the load fails
        closed: ``_templates`` is reset to an empty list and a warning
        is logged so the user knows their templates were discarded
        rather than silently loaded from a tampered file.
        """
        if not self._path.exists():
            self._templates = []
            return
        try:
            from voice_typer.server.config import _secure_read_text

            raw = _secure_read_text(self._path)
            data = json.loads(raw)
            if isinstance(data, list):
                self._templates = data
            elif isinstance(data, dict) and "templates" in data:
                self._templates = data["templates"]
            else:
                self._templates = []
            log.info("[TEMPLATES] Loaded %d templates from %s", len(self._templates), self._path)
        except Exception as exc:
            log.warning("[TEMPLATES] Failed to load from %s: %s", self._path, exc)
            self._templates = []

    def _save(self) -> None:
        """Save templates to JSON file.

        NEW-SEC-008: uses the shared _secure_atomic_write which applies
        O_NOFOLLOW on POSIX to prevent symlink TOCTOU attacks.

        M-62: previously this method caught *all* exceptions and
        silently logged them, returning ``None`` to callers. That
        meant a disk failure left the in-memory ``_templates`` list
        (already mutated by ``add``/``update``/``delete``) out of
        sync with what was actually on disk — the user's edit
        appeared to succeed (no error surfaced) but the next process
        restart would load the stale on-disk state and the edit
        would be lost. Now we log the error AND re-raise so callers
        can roll back their in-memory mutation and the IPC layer can
        surface the failure to the renderer.
        """
        from voice_typer.server.config import _secure_atomic_write

        self._path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(
            {"templates": self._templates},
            indent=2,
            ensure_ascii=False,
        )
        try:
            _secure_atomic_write(self._path, content)
        except Exception:
            # M-62: log then re-raise so callers can roll back.
            # G4-H-38: use log.exception so the traceback is captured
            # automatically via sys.exc_info().
            log.exception("[TEMPLATES] Failed to save")
            raise
        log.debug("[TEMPLATES] Saved %d templates", len(self._templates))

    def add(self, trigger: str, output: str, *, match_mode: str = "exact") -> dict | None:
        """Add a new template. Returns the created template dict, or
        ``None`` if rejected.

        G4-M-38: enforces SEC-011-style caps:
          - Per-field length cap: ``MAX_TRIGGER_LENGTH``,
            ``MAX_OUTPUT_LENGTH``. Oversized entries are rejected with
            a logged warning.
          - Total count cap: ``MAX_TEMPLATES``. Once the cap is reached
            the new template is dropped (rejected with a warning).

        M-62: persists first-then-mutates with rollback. If ``_save``
        raises, the appended entry is popped back off so the
        in-memory state stays consistent with the on-disk state.
        """
        trigger_stripped = trigger.strip() if isinstance(trigger, str) else str(trigger).strip()
        output_str = output if isinstance(output, str) else str(output)
        if len(trigger_stripped) > MAX_TRIGGER_LENGTH:
            log.warning(
                "[TEMPLATES] Trigger exceeds MAX_TRIGGER_LENGTH (%d > %d), rejecting",
                len(trigger_stripped),
                MAX_TRIGGER_LENGTH,
            )
            return None
        if len(output_str) > MAX_OUTPUT_LENGTH:
            log.warning(
                "[TEMPLATES] Output exceeds MAX_OUTPUT_LENGTH (%d > %d), rejecting",
                len(output_str),
                MAX_OUTPUT_LENGTH,
            )
            return None
        if len(self._templates) >= MAX_TEMPLATES:
            log.warning(
                "[TEMPLATES] Template count at MAX_TEMPLATES cap (%d), rejecting new template",
                MAX_TEMPLATES,
            )
            return None
        template = {
            "trigger": trigger_stripped,
            "output": output_str,
            "match_mode": match_mode,  # "exact" or "contains"
            "created_at": datetime.now().isoformat(),
        }
        self._templates.append(template)
        try:
            self._save()
        except Exception:
            # Rollback: remove the template we just appended.
            # Use identity check (not equality) in case the template
            # dict happens to equal an earlier entry.
            for i in range(len(self._templates) - 1, -1, -1):
                if self._templates[i] is template:
                    del self._templates[i]
                    break
            raise
        return template

    def update(self, index: int, trigger: str, output: str, *, match_mode: str = "exact") -> dict | None:
        """Update a template by index. Returns the updated template or None.

        M-62: snapshots the original field values and restores them
        on save failure so the in-memory state stays consistent with
        the on-disk state.
        """
        if not (0 <= index < len(self._templates)):
            return None
        entry = self._templates[index]
        # Snapshot originals for rollback.
        old_trigger = entry.get("trigger")
        old_output = entry.get("output")
        old_match_mode = entry.get("match_mode")
        entry["trigger"] = trigger.strip()
        entry["output"] = output
        entry["match_mode"] = match_mode
        try:
            self._save()
        except Exception:
            entry["trigger"] = old_trigger
            entry["output"] = old_output
            entry["match_mode"] = old_match_mode
            raise
        return entry

    def delete(self, index: int) -> bool:
        """Delete a template by index.

        M-62: snapshots the deleted entry and re-inserts it at the
        same index on save failure so the in-memory state stays
        consistent with the on-disk state.
        """
        if not (0 <= index < len(self._templates)):
            return False
        removed = self._templates.pop(index)
        try:
            self._save()
        except Exception:
            # Rollback: re-insert at the original index.
            self._templates.insert(index, removed)
            raise
        return True

    # ── Import / Export ───────────────────────────────────────────────

    def export_json(self) -> str:
        """Export templates as a JSON string."""
        return json.dumps({"templates": self._templates}, indent=2, ensure_ascii=False)

    def import_json(self, json_str: str) -> int:
        """Import templates from a JSON string. Returns number imported.

        G4-M-38: enforces SEC-011-style caps:
          - Drops templates whose trigger exceeds
            ``MAX_TRIGGER_LENGTH`` or output exceeds
            ``MAX_OUTPUT_LENGTH`` (mirrors
            ``text_cleanup._load_external_corrections``).
          - Truncates the import if it would exceed ``MAX_TEMPLATES``.
          - Logs a single warning summarising the dropped count.

        M-62: snapshots the list before appending and restores it on
        save failure so the in-memory state stays consistent with the
        on-disk state.
        """
        try:
            data = json.loads(json_str)
            templates = data if isinstance(data, list) else data.get("templates", [])
            to_add: list[dict] = []
            dropped = 0
            for t in templates:
                if not isinstance(t, dict) or "trigger" not in t or "output" not in t:
                    continue
                trigger_raw = t.get("trigger", "")
                output_raw = t.get("output", "")
                trigger_str = trigger_raw if isinstance(trigger_raw, str) else str(trigger_raw)
                output_str = output_raw if isinstance(output_raw, str) else str(output_raw)
                # Use the stripped length for the trigger cap to match
                # the add() behavior (which strips before storing).
                if len(trigger_str.strip()) > MAX_TRIGGER_LENGTH:
                    dropped += 1
                    continue
                if len(output_str) > MAX_OUTPUT_LENGTH:
                    dropped += 1
                    continue
                to_add.append(t)
            if dropped:
                log.warning(
                    "[TEMPLATES] Dropped %d templates from import (oversized)",
                    dropped,
                )
            # Total-count cap: truncate to fit within MAX_TEMPLATES.
            current = len(self._templates)
            available = MAX_TEMPLATES - current
            if available <= 0:
                log.warning(
                    "[TEMPLATES] Template count at MAX_TEMPLATES cap (%d), dropping all %d imported templates",
                    MAX_TEMPLATES,
                    len(to_add),
                )
                return 0
            if len(to_add) > available:
                log.warning(
                    "[TEMPLATES] Import exceeds MAX_TEMPLATES cap, truncating %d -> %d",
                    len(to_add),
                    available,
                )
                to_add = to_add[:available]
            if not to_add:
                return 0
            # Snapshot for rollback.
            old_len = len(self._templates)
            self._templates.extend(to_add)
            try:
                self._save()
            except Exception:
                # Rollback: truncate back to the pre-import length.
                del self._templates[old_len:]
                raise
            return len(to_add)
        except Exception:
            log.exception("[TEMPLATES] Import failed")
            return 0

    # ── Matching ─────────────────────────────────────────────────────

    def match(self, text: str) -> str | None:
        """Try to match *text* against any template trigger.

        Returns the expanded output text (with variables substituted)
        if a match is found, or None if no template matches.

        Matching rules:
        - Whitespace-normalized, case-insensitive comparison
        - "exact" mode: the whole text must match the trigger
        - "contains" mode: the trigger must be found anywhere in the text
        - Shortest trigger wins when multiple templates match
        """
        if not text or not self._templates:
            return None

        normalized = re.sub(r"\s+", " ", text.strip()).lower()

        best_match: dict | None = None
        best_len = float("inf")

        for t in self._templates:
            trigger = t.get("trigger", "")
            if not trigger:
                continue
            trigger_norm = re.sub(r"\s+", " ", trigger.strip()).lower()
            mode = t.get("match_mode", "exact")

            matched = trigger_norm in normalized if mode == "contains" else normalized == trigger_norm

            if matched and len(trigger_norm) < best_len:
                best_match = t
                best_len = len(trigger_norm)

        if best_match is not None:
            output = best_match["output"]
            return substitute_variables(output)

        return None
