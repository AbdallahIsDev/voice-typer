"""Templates service mixin."""

import logging
from typing import TYPE_CHECKING

from voice_typer.server.service._app_internals import (
    app_template_manager,
    set_app_template_manager,
)
from voice_typer.server.service._base import ServiceMixinBase

if TYPE_CHECKING:
    # T1-F9: imported only under ``TYPE_CHECKING`` so the annotation
    from voice_typer.server.templates import TemplateManager

log = logging.getLogger(__name__)


class TemplateMixin(ServiceMixinBase):
    """Template-domain service methods."""

    # previously this method read from a non-existent

    def _template_manager(self) -> "TemplateManager":
        """Lazily obtain (or create) the app's TemplateManager."""
        app = self._app
        tm = app_template_manager(app)
        if tm is None:
            from voice_typer.server.templates import TemplateManager

            tm = TemplateManager()
            set_app_template_manager(app, tm)
        return tm

    def get_templates(self) -> list[dict]:
        """Return saved templates from the persistent template store.

        Returns a list of dicts with keys: trigger, output, match_mode,
        """
        try:
            tm = self._template_manager()
            # Each template dict from TemplateManager has the shape
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
        """Replace all templates in the persistent store."""
        try:
            tm = self._template_manager()
            # Normalize and replace.  We don't call tm.add/update/delete
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
            # Use the manager's atomic full-replace method so the
            tm.replace_all(normalized)
            log.info("[SERVICE] Saved %d templates", len(normalized))
            return True
        except Exception as exc:
            log.error("[SERVICE] save_templates failed: %s", exc, exc_info=True)
            return False
