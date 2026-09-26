"""Service app-internals accessors."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any, Protocol, cast

if TYPE_CHECKING:
    from voice_typer.server.templates import TemplateManager


class _AppInternalAttributes(Protocol):
    """Private attribute surface of ``LausuApp`` (subset).
    in ``LausuApp.__init__`` / its construction mixins.
    """

    _config_mutation_lock: threading.RLock
    _microphones: list[dict]
    _template_manager: TemplateManager | None
    _crash_recovery: Any
    _llm_polisher: Any
    _cloud_engine: Any


def _internal(app: object) -> _AppInternalAttributes:
    """Assert the private-attribute surface (single cast point)."""
    return cast(_AppInternalAttributes, app)


def app_config_mutation_lock(app: object) -> threading.RLock:
    """Return the app-wide config-mutation lock."""
    return _internal(app)._config_mutation_lock


def app_microphones(app: object) -> list[dict]:
    """Return the app's cached microphone list (list of device dicts)."""
    return getattr(_internal(app), "_microphones", [])


def set_app_microphones(app: object, microphones: list[dict]) -> None:
    """Replace the app's cached microphone list (device hot-plug / refresh)."""
    _internal(app)._microphones = microphones


def app_crash_recovery(app: object) -> Any:
    """Return the app's ``CrashRecovery`` subsystem (may be ``None``)."""
    return _internal(app)._crash_recovery


def app_template_manager(app: object) -> TemplateManager | None:
    """Return the app's ``TemplateManager``, or ``None`` if not created."""
    return getattr(app, "_template_manager", None)


def set_app_template_manager(app: object, manager: TemplateManager) -> None:
    """Store ``manager`` as the app's (single) ``TemplateManager``."""
    _internal(app)._template_manager = manager


def invalidate_llm_polisher(app: object) -> None:
    """Drop the cached LLM polish engine so the next request rebuilds."""
    _internal(app)._llm_polisher = None


def invalidate_cloud_engine(app: object) -> None:
    """Drop the cached cloud transcription engine so the next request
    rebuilds with the current config."""
    _internal(app)._cloud_engine = None


__all__ = [
    "app_config_mutation_lock",
    "app_crash_recovery",
    "app_microphones",
    "app_template_manager",
    "invalidate_cloud_engine",
    "invalidate_llm_polisher",
    "set_app_microphones",
    "set_app_template_manager",
]
