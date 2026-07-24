"""Template domain mixin for VoiceTyperService.

Extracted verbatim from the original ``service.py`` god class
(ARCH-005 split). Template CRUD delegated to
:class:`voice_typer.server.templates.TemplateManager`.
"""

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # T1-F9: imported only under ``TYPE_CHECKING`` so the annotation
    # ``-> "TemplateManager"`` on :meth:`_template_manager` resolves at
    # type-check time without forcing a runtime import (and a possible
    # cycle) of :mod:`voice_typer.server.templates`.
    from voice_typer.server.templates import TemplateManager

log = logging.getLogger(__name__)


class TemplateMixin:
    """Template-domain service methods.

    Wraps the persistent :class:`TemplateManager` so the renderer's
    template store survives reinstalls / app-data resets (NEW-UX-008).
    """

    # ── Templates (#6, NEW-UX-008) ──────────────────────────────
    #
    # NEW-UX-008: previously this method read from a non-existent
    # ``config.templates_data`` attribute, so it always returned an
    # empty list — and ``save_templates`` set the attribute on the
    # dataclass instance but never persisted it (``dataclasses.asdict``
    # only serializes declared fields, so the dynamic attribute was
    # silently dropped on save).  As a result the renderer kept
    # templates ONLY in localStorage, and they were lost on reinstall
    # or app-data reset.
    #
    # The fix delegates to the existing ``TemplateManager`` which
    # already persists to ``voice-typer-templates.json`` in the
    # Python config dir (``~/.voice-typer`` on POSIX,
    # ``%APPDATA%\voice-typer`` on Windows).  This file survives
    # Electron userData resets and reinstalls.

    def _template_manager(self) -> "TemplateManager":
        """Lazily obtain (or create) the app's TemplateManager."""
        app = self._app
        tm = getattr(app, "_template_manager", None)
        if tm is None:
            from voice_typer.server.templates import TemplateManager

            tm = TemplateManager()
            app._template_manager = tm
        return tm

    def get_templates(self) -> list[dict]:
        """Return saved templates from the persistent template store.

        Returns a list of dicts with keys: trigger, output, match_mode,
        created_at (optional).
        """
        try:
            tm = self._template_manager()
            # Each template dict from TemplateManager has the shape
            # {trigger, output, match_mode, created_at?}.  Strip any
            # internal fields and return a plain list for IPC.
            return [
                {
                    "trigger": t.get("trigger", ""),
                    "output": t.get("output", ""),
                    "match_mode": t.get("match_mode", "exact"),
                }
                for t in tm.templates
            ]
        except Exception as exc:
            log.error("[SERVICE] get_templates failed: %s", exc, exc_info=True)
            return []

    def save_templates(self, templates: list[dict]) -> bool:
        """Replace all templates in the persistent store.

        NEW-UX-008: full-replace semantics — the renderer sends the
        complete list after each add/edit/delete, and we persist the
        whole list atomically via TemplateManager._save (which uses
        _secure_atomic_write — O_NOFOLLOW on POSIX, temp+rename).
        """
        try:
            tm = self._template_manager()
            # Normalize and replace.  We don't call tm.add/update/delete
            # individually because the renderer already has the full
            # list; doing N writes would be N× disk I/O for one user
            # action.  Direct list replacement is atomic and fast.
            normalized: list[dict] = []
            for t in templates or []:
                if not isinstance(t, dict):
                    continue
                trigger = str(t.get("trigger", "")).strip()
                output = str(t.get("output", ""))
                match_mode = str(t.get("match_mode", "exact"))
                if not trigger or not output:
                    continue
                if match_mode not in ("exact", "contains"):
                    match_mode = "exact"
                normalized.append(
                    {
                        "trigger": trigger,
                        "output": output,
                        "match_mode": match_mode,
                    }
                )
            # Use the manager's internal list + _save so the on-disk
            # format matches what TemplateManager._load expects (a
            # dict with a "templates" key).
            tm._templates = normalized
            tm._save()
            log.info("[SERVICE] Saved %d templates", len(normalized))
            return True
        except Exception as exc:
            log.error("[SERVICE] save_templates failed: %s", exc, exc_info=True)
            return False
