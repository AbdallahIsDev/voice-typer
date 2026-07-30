"""Privacy IPC handler mixin (STUB — UE-15, 2026-07-30).

CR-87 / CR-88 (GDPR right-to-delete / right-to-export): the prior
implementation exposed two IPC commands — ``delete_all_personal_data``
(GDPR Art. 17 right-to-erasure) and ``export_gdpr_bundle`` (GDPR Art. 20
right-to-data-portability) — as ``_handle_delete_all_personal_data`` /
``_handle_export_gdpr_bundle`` on :class:`PrivacyHandlersMixin`.

UE-15 (2026-07-30): both handler methods were REMOVED — the Tauri host
now invokes the underlying service-layer methods
(``service.delete_all_personal_data`` / ``service.export_gdpr_bundle``,
both implemented by Fix-D) via dedicated Rust commands with their own
allowlist entries and consent prompts, rather than bridging through the
generic Python dispatch path. The Python-side service methods still
exist (called from the Rust bridge); only the IPC dispatch routes were
deleted.

The mixin class itself is retained as an empty subclass of
:class:`HandlerBase` so that existing imports
(``from voice_typer.server.handlers import PrivacyHandlersMixin``) and
the ``IPCServer`` MRO continue to resolve without a refactor. If a
future change re-introduces privacy-specific IPC handlers, they should
land here.

The historical docstring claimed that ``delete_all_personal_data`` and
``export_gdpr_bundle`` were registered in ``_COMMAND_REGISTRY`` — that
was already stale before UE-15 (the registry entries were removed in
lockstep with the Tauri migration). This file now reflects reality.
"""

from voice_typer.server.handlers._base import HandlerBase  # noqa: F401


class PrivacyHandlersMixin(HandlerBase):
    """Mixin: privacy / GDPR IPC handlers (STUB — UE-15).

    UE-15 (2026-07-30): the two handler methods that previously lived
    here (``_handle_delete_all_personal_data`` and
    ``_handle_export_gdpr_bundle``) were removed — the Tauri host now
    invokes the underlying service methods via dedicated Rust commands.
    The class is retained as an empty placeholder so the
    ``IPCServer`` MRO and the ``from voice_typer.server.handlers
    import PrivacyHandlersMixin`` re-export continue to resolve.

    Inherits ``service`` / ``app`` / ``_send`` annotations from
    :class:`HandlerBase`.
    """


__all__ = ["PrivacyHandlersMixin"]
