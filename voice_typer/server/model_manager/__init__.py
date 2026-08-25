"""ModelManager package - ASR backend lifecycle owner.

Facade preserving the historical
``voice_typer.server.model_manager`` import path; implementation
is split into concern mixins composed in :mod:`.manager`."""

from .manager import ModelManager

__all__ = ["ModelManager"]
