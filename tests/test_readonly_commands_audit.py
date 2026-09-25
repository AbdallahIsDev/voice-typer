"""Membership audit for ``_READONLY_COMMANDS``.

The dispatcher bypasses ``_dispatch_lock`` for members of
``_READONLY_COMMANDS`` so long-running mutating handlers cannot block status
and list polls. That bypass is only safe when the handler and everything it
calls are free of shared-state mutation. This module keeps the classification
honest: a new ``get_*`` command must either be marked as a pure read or be
listed below as a documented mutator with a reason.
"""

from __future__ import annotations

from voice_typer.server.ipc.registry import (
    _COMMAND_REGISTRY,
    _INSTANT_CONTROL_COMMANDS,
    _READONLY_COMMANDS,
    _SELF_SERIALIZED_COMMANDS,
)

# ``get_*`` commands that DO mutate shared state, so they must stay behind the
# dispatch lock. Each entry needs a one-line reason; an empty map means every
# ``get_*`` command currently registered is a pure read.
_DOCUMENTED_MUTATING_GET_COMMANDS: dict[str, str] = {}


def test_every_get_command_is_classified() -> None:
    """Every ``get_*`` command is either readonly or a documented mutator."""
    unclassified = sorted(
        cmd
        for cmd in _COMMAND_REGISTRY
        if cmd.startswith("get_") and cmd not in _READONLY_COMMANDS and cmd not in _DOCUMENTED_MUTATING_GET_COMMANDS
    )
    assert unclassified == [], (
        "New get_* IPC commands must be classified as pure reads in "
        "_READONLY_COMMANDS or as documented mutators in "
        f"_DOCUMENTED_MUTATING_GET_COMMANDS (offenders: {unclassified})."
    )


def test_documented_mutators_are_not_marked_readonly() -> None:
    """A documented mutator must never be admitted to the readonly set."""
    overlap = sorted(set(_DOCUMENTED_MUTATING_GET_COMMANDS) & _READONLY_COMMANDS)
    assert overlap == [], f"Commands listed as mutating must not be in _READONLY_COMMANDS: {overlap}."


def test_readonly_commands_are_registered() -> None:
    """Every readonly entry names a real registered command."""
    unknown = sorted(_READONLY_COMMANDS - set(_COMMAND_REGISTRY))
    assert unknown == [], f"_READONLY_COMMANDS contains unregistered commands: {unknown}."


def test_readonly_does_not_overlap_instant_controls() -> None:
    """Lock-bypassing mutation controls stay separate from the readonly set."""
    overlap = sorted(_READONLY_COMMANDS & _INSTANT_CONTROL_COMMANDS)
    assert overlap == [], f"Pure reads and instant download controls must not share members: {overlap}."


def test_self_serialized_commands_are_registered_and_disjoint() -> None:
    """Self-serialized bypass members name real registered commands and
    share no members with the readonly or instant sets (they are real
    I/O-owning mutators with their own concurrency guards, neither pure
    reads nor single-flag instant controls)."""
    unknown = sorted(_SELF_SERIALIZED_COMMANDS - set(_COMMAND_REGISTRY))
    assert unknown == [], f"_SELF_SERIALIZED_COMMANDS contains unregistered commands: {unknown}."
    overlap_ro = sorted(_READONLY_COMMANDS & _SELF_SERIALIZED_COMMANDS)
    assert overlap_ro == [], f"Pure reads and self-serialized commands must not share members: {overlap_ro}."
    overlap_instant = sorted(_INSTANT_CONTROL_COMMANDS & _SELF_SERIALIZED_COMMANDS)
    assert overlap_instant == [], (
        f"Instant controls and self-serialized commands must not share members: {overlap_instant}."
    )
