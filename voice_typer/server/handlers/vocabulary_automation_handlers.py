"""the matching ``_COMMAND_REGISTRY`` cleanup. The service-layer"""

from voice_typer.server.handlers._base import HandlerBase  # noqa: F401


class VocabularyAutomationHandlersMixin(HandlerBase):
    """allowlist + ``_COMMAND_REGISTRY`` entries were dropped in"""


__all__ = ["VocabularyAutomationHandlersMixin"]
