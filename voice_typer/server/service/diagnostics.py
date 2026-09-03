"""Diagnostics domain mixin for VoiceTyperService.

Extracted verbatim from the original ``service.py`` god class
( / Phase 4.5 spaghetti split). Owns the support-bundle export
that doesn't belong to a single domain mixin:

* :meth:`DiagnosticsMixin.export_diagnostics` —  diagnostic
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
    """Support-bundle export ().

    Wraps :class:`CrashRecovery.create_diagnostic_bundle` and
    redacts any exception text via :func:`redact_secret` /
    :func:`redact_url` so a support-ticket bundle never leaks an API
    key or endpoint URL.
    """

    # Export diagnostics ─────────────────────────────────

    def export_diagnostics(self) -> dict:
        """Create a diagnostic bundle for support.

                Delegates to :func:`voice_typer.server.diagnostics_export.create_diagnostic_bundle`
        ( — the bundle-building body was extracted out of
                :class:`CrashRecovery` so that module can focus on its core
                recovery-entry storage concern). Returns
                ``{"success": bool, "path": str}`` on success or
                ``{"success": False, "message": str}`` on failure.

                When ``self._app._crash_recovery`` is ``None`` the
                previous implementation silently fell back to constructing a
                fresh ``CrashRecovery()`` instance and exporting an *empty*
                bundle (no recovery entries, no recent crashes). The user got
                a ``{"success": True, "path": ...}`` response pointing at a
                useless file. Now we refuse to export and return a clear
                failure message instead, with a WARNING log so the empty-
                bundle regression is visible in support tickets.
        """
        try:
            # Direct attribute access (NOT ``getattr``) is correct
            # here — ``self._app`` is duck-typed at runtime as a
            # ``VoiceTyperApp`` which always sets ``self._crash_recovery``
            # in ``__init__`` (possibly to ``None`` during early startup
            # or after a failed init). The previous comment claimed
            # ``getattr`` was used "so the static type checker doesn't
            # flag the access" but the code already used direct access —
            # the comment was stale and has been corrected. ``AppProtocol``
            # does not declare ``_crash_recovery`` (ADR-0008-§3.1 — the
            # protocol surface is intentionally minimal), so type-checkers
            # that run on the mixin see an unknown-attribute warning on
            # this line; that's an accepted trade-off (the mixin is
            # composed into ``VoiceTyperService`` whose ``self._app`` is
            # the concrete ``VoiceTyperApp``, not the narrow
            # ``AppProtocol``).
            recovery = getattr(self._app, "_crash_recovery")  # noqa: B009 — attr deliberately not on AppProtocol (ADR-0008-§3.1); direct access fails pyrefly
            if recovery is None:
                # Refuse to export rather than silently producing
                # an empty bundle from a fresh ``CrashRecovery()``. The
                # WARNING level (not ERROR) reflects that this is a
                # degraded-but-recoverable state — the app may still be
                # mid-startup, or the crash-recovery subsystem failed to
                # initialize but the rest of the app is functional.
                log.warning(
                    "[DIAGNOSTICS] export_diagnostics refused: "
                    "self._app._crash_recovery is None — cannot export "
                    "diagnostics (crash-recovery subsystem unavailable)"
                )
                return {
                    "success": False,
                    "message": "Crash recovery unavailable — cannot export diagnostics",
                }
            # call the diagnostics_export module directly instead
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
            log.exception("export_diagnostics failed: %s", exc)
            return {"success": False, "message": redact_secret(redact_url(str(exc)))}


__all__ = ["DiagnosticsMixin"]
