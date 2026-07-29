"""Diagnostics domain mixin for VoiceTyperService.

Extracted verbatim from the original ``service.py`` god class
(DT-26 / Phase 4.5 spaghetti split). Owns the support-bundle export
that doesn't belong to a single domain mixin:

* :meth:`DiagnosticsMixin.export_diagnostics` — PROD-010 diagnostic
  bundle (redacted PII, suitable for attaching to a support ticket).

This is intentionally a SEPARATE mixin from :class:`PrivacyMixin`
because the two methods share NO state (no shared file set, no shared
keychain logic) and have OPPOSITE redaction policies:
``export_diagnostics`` redacts PII for a support ticket, while
``PrivacyMixin.export_gdpr_bundle`` exports the user's own data
verbatim (GDPR Art. 20 portability). Keeping them separate prevents
a future contributor from accidentally sharing a "personal files"
constant between the two paths.

Every public method name and signature is preserved verbatim; the
mixin is composed via multiple inheritance so
``VoiceTyperService.export_diagnostics`` resolves to
``DiagnosticsMixin.export_diagnostics`` (MRO).
"""

import logging

from voice_typer.server._secrets import redact_secret, redact_url
from voice_typer.server.service._base import ServiceMixinBase

log = logging.getLogger(__name__)


class DiagnosticsMixin(ServiceMixinBase):
    """Support-bundle export (PROD-010).

    Wraps :class:`CrashRecovery.create_diagnostic_bundle` and
    redacts any exception text via :func:`redact_secret` /
    :func:`redact_url` so a support-ticket bundle never leaks an API
    key or endpoint URL.
    """

    # ── PROD-010: Export diagnostics ─────────────────────────────────

    def export_diagnostics(self) -> dict:
        """PROD-010: Create a diagnostic bundle for support.

        Delegates to :func:`voice_typer.server.diagnostics_export.create_diagnostic_bundle`
        (DR-27 — the bundle-building body was extracted out of
        :class:`CrashRecovery` so that module can focus on its core
        recovery-entry storage concern). Returns
        ``{"success": bool, "path": str}`` on success or
        ``{"success": False, "message": str}`` on failure.
        """
        try:
            # Use ``getattr`` instead of direct attribute access so the
            # static type checker doesn't flag the access (``self._app``
            # is typed as :class:`AppProtocol` which doesn't declare
            # ``_crash_recovery`` per ADR-0008-§3.1 — see
            # ``providers.py`` for the full rationale). ``getattr`` returns
            # ``Any`` to the type checker and is functionally equivalent at
            # runtime.
            recovery = self._app._crash_recovery
            if recovery is None:
                from voice_typer.server.crash_recovery import CrashRecovery

                recovery = CrashRecovery()
            # DR-27: call the diagnostics_export module directly instead
            # of going through the ``CrashRecovery.create_diagnostic_bundle``
            # delegate. Same observable behavior — the delegate on
            # ``CrashRecovery`` is preserved for back-compat with other
            # callers (tests, CLI), but this mixin is the primary in-process
            # caller and now uses the canonical entry point.
            from voice_typer.server.diagnostics_export import (
                create_diagnostic_bundle,
            )

            path = create_diagnostic_bundle(recovery)
            if path:
                return {"success": True, "path": path}
            else:
                return {"success": False, "message": "Failed to create diagnostic bundle"}
        except Exception as exc:
            log.error("export_diagnostics failed: %s", exc)
            return {"success": False, "message": redact_secret(redact_url(str(exc)))}


__all__ = ["DiagnosticsMixin"]
