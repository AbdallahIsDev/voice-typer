"""VoiceTyperService model mixin package.

Facade preserving the historical ``voice_typer.server.service.model``
import path; implementation split into concern mixins."""

from ._constants import _MODEL_STATUS_CACHE_TTL_S, _PARAKEET_REASON_MESSAGES
from .mixin import ModelMixin

__all__ = ["ModelMixin", "_MODEL_STATUS_CACHE_TTL_S", "_PARAKEET_REASON_MESSAGES"]
