"""Typed access to the app attributes excluded from ``AppProtocol``.

ADR-0008 §3.1 keeps :class:`~voice_typer.server.providers.AppProtocol`
minimal, and ``tests/test_di_providers.py`` enforces that boundary: the
private ``VoiceTyperApp`` attributes the service layer still touches
(``_config_mutation_lock``, ``_microphones``, ``_template_manager``,
``_crash_recovery``, ``_llm_polisher``, ``_cloud_engine``) are
deliberately NOT declared on the protocol. Before this module existed,
every service call site re-implemented the same workaround —
``getattr(app, "_x")`` / ``setattr(app, "_x", v)`` with an inline
``# noqa: B009`` / ``# noqa: B010`` marker — so the "attribute is real
but off-protocol" knowledge was copy-pasted across the service mixins.

This module is the single home for that knowledge:

* :class:`_AppInternalAttributes` documents the private surface as a
  structural ``Protocol`` (the concrete :class:`VoiceTyperApp`
  satisfies it; the protocol is what the one ``cast`` below asserts).
* The accessor functions below are the only code that touches those
  attributes. Every service-layer call site goes through them, so a
  future rename of any attribute is a one-file change.

The accessors keep the runtime semantics of the ``getattr`` /
``setattr`` blocks they replace:

* reads of attributes that always exist on ``VoiceTyperApp`` raise on
  a missing attribute (``getattr`` had no default either);
* :func:`app_template_manager` tolerates a missing attribute (the
  manager is created lazily; fakes without it must read ``None``);
* :func:`app_microphones` tolerates a missing attribute (the list is
  empty before the first enumeration; fakes without it read ``[]`` —
  the pre-split ``getattr(app, "_microphones", [])`` semantics);
* writes are plain attribute assignments — identical to
  ``setattr(app, "_x", value)``.

The ``cast`` in ``_internal`` is the documented, single suppression
point: the service layer knows the concrete app carries these
attributes, and the type system cannot express "superset of
``AppProtocol``" without re-declaring them on the protocol (which
ADR-0008 §3.1 forbids).
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any, Protocol, cast

if TYPE_CHECKING:
    from voice_typer.server.templates import TemplateManager


class _AppInternalAttributes(Protocol):
    """Private attribute surface of ``VoiceTyperApp`` (subset).

    Only the attributes the service-layer accessors below read or
    write are declared. ``_template_manager`` is a lazily-created
    property on the app (``app_lazy_hub``); ``_llm_polisher`` /
    ``_cloud_engine`` are ``None`` until first use; the rest are bound
    in ``VoiceTyperApp.__init__`` / its construction mixins.
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
    """Return the app-wide config-mutation lock.

    Guards every config write path (``apply_config``, config reset,
    onboarding apply) so concurrent ``set_config`` IPC calls cannot
    interleave attribute writes with saves.
    """
    return _internal(app)._config_mutation_lock


def app_microphones(app: object) -> list[dict]:
    """Return the app's cached microphone list (list of device dicts).

    Tolerates a missing attribute (reads ``[]``): the list is empty
    before the first enumeration, and test fakes may not bind it —
    this preserves the pre-split ``getattr(app, "_microphones", [])``
    semantics at the ``load_microphones`` call site.
    """
    return getattr(_internal(app), "_microphones", [])


def set_app_microphones(app: object, microphones: list[dict]) -> None:
    """Replace the app's cached microphone list (device hot-plug / refresh)."""
    _internal(app)._microphones = microphones


def app_crash_recovery(app: object) -> Any:
    """Return the app's ``CrashRecovery`` subsystem (may be ``None``).

    ``None`` during early startup or after a failed crash-recovery
    init; callers must handle it.
    """
    return _internal(app)._crash_recovery


def app_template_manager(app: object) -> TemplateManager | None:
    """Return the app's ``TemplateManager``, or ``None`` if not created.

    The manager is created lazily on first template use, so a missing
    attribute (fresh app, minimal test fake) reads as ``None``.
    """
    return getattr(app, "_template_manager", None)


def set_app_template_manager(app: object, manager: TemplateManager) -> None:
    """Store ``manager`` as the app's (single) ``TemplateManager``."""
    _internal(app)._template_manager = manager


def invalidate_llm_polisher(app: object) -> None:
    """Drop the cached LLM polish engine so the next request rebuilds.

    Used when the config changes (reset-to-defaults) — the next polish
    must rebuild with the new settings instead of reusing a client
    bound to the old ones.
    """
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
