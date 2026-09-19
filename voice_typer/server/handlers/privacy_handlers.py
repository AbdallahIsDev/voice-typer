"""``export_gdpr_bundle`` were registered in ``_COMMAND_REGISTRY``, that"""

from voice_typer.server.handlers._base import HandlerBase  # noqa: F401


class PrivacyHandlersMixin(HandlerBase):
    """Mixin: privacy / GDPR IPC handlers (STUB, )."""


__all__ = ["PrivacyHandlersMixin"]
