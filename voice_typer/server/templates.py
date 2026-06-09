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

import json
import logging
import os
import re
import getpass
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

TEMPLATES_FILENAME = "voice-typer-templates.json"

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
    """Get username safely, returning 'user' on any failure."""
    try:
        name = getpass.getuser()
        return str(name) if name and isinstance(name, str) else "user"
    except Exception:
        return "user"


# ─── Template manager ──────────────────────────────────────────────────


class TemplateManager:
    """Manages voice templates: CRUD, persistence, matching."""

    def __init__(self, config_dir: Optional[Path] = None):
        if config_dir is None:
            from voice_typer.server.config import _config_dir
            config_dir = _config_dir()
        self._path = config_dir / TEMPLATES_FILENAME
        self._templates: list[dict] = []
        self._load()

    # ── Persistence ──────────────────────────────────────────────────

    def _load(self) -> None:
        """Load templates from JSON file."""
        if not self._path.exists():
            self._templates = []
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
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
        """Save templates to JSON file."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps({"templates": self._templates}, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            tmp.replace(self._path)
            log.debug("[TEMPLATES] Saved %d templates", len(self._templates))
        except Exception as exc:
            log.error("[TEMPLATES] Failed to save: %s", exc)

    # ── CRUD ─────────────────────────────────────────────────────────

    @property
    def templates(self) -> list[dict]:
        """Return a copy of the template list."""
        return list(self._templates)

    def add(self, trigger: str, output: str, *, match_mode: str = "exact") -> dict:
        """Add a new template. Returns the created template dict."""
        template = {
            "trigger": trigger.strip(),
            "output": output,
            "match_mode": match_mode,  # "exact" or "contains"
            "created_at": datetime.now().isoformat(),
        }
        self._templates.append(template)
        self._save()
        return template

    def update(self, index: int, trigger: str, output: str, *, match_mode: str = "exact") -> Optional[dict]:
        """Update a template by index. Returns the updated template or None."""
        if 0 <= index < len(self._templates):
            self._templates[index]["trigger"] = trigger.strip()
            self._templates[index]["output"] = output
            self._templates[index]["match_mode"] = match_mode
            self._save()
            return self._templates[index]
        return None

    def delete(self, index: int) -> bool:
        """Delete a template by index."""
        if 0 <= index < len(self._templates):
            del self._templates[index]
            self._save()
            return True
        return False

    # ── Import / Export ───────────────────────────────────────────────

    def export_json(self) -> str:
        """Export templates as a JSON string."""
        return json.dumps({"templates": self._templates}, indent=2, ensure_ascii=False)

    def import_json(self, json_str: str) -> int:
        """Import templates from a JSON string. Returns number imported."""
        try:
            data = json.loads(json_str)
            templates = data if isinstance(data, list) else data.get("templates", [])
            count = 0
            for t in templates:
                if isinstance(t, dict) and "trigger" in t and "output" in t:
                    self._templates.append(t)
                    count += 1
            if count:
                self._save()
            return count
        except Exception as exc:
            log.error("[TEMPLATES] Import failed: %s", exc)
            return 0

    # ── Matching ─────────────────────────────────────────────────────

    def match(self, text: str) -> Optional[str]:
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

        best_match: Optional[dict] = None
        best_len = float("inf")

        for t in self._templates:
            trigger = t.get("trigger", "")
            if not trigger:
                continue
            trigger_norm = re.sub(r"\s+", " ", trigger.strip()).lower()
            mode = t.get("match_mode", "exact")

            matched = False
            if mode == "contains":
                matched = trigger_norm in normalized
            else:  # exact
                matched = normalized == trigger_norm

            if matched and len(trigger_norm) < best_len:
                best_match = t
                best_len = len(trigger_norm)

        if best_match is not None:
            output = best_match["output"]
            return substitute_variables(output)

        return None
