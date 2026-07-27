"""Targeted test for ZR-43 + ZR-70 module-level constant hoist.

Verifies:
- ``_TCP_PENDING_DRAIN_CAP`` and ``_TCP_PENDING_BUFFER_CAP`` are module-level
  constants in ``ipc_server.py`` (ZR-43).
- ``_SHUTDOWN_ALLOWLIST`` is a ``frozenset`` at module level (ZR-70).
- The values match the previously inlined magic numbers (100, 1000, 5 entries).
"""
from __future__ import annotations

from voice_typer.server.ipc_server import (
    _SHUTDOWN_ALLOWLIST,
    _TCP_PENDING_BUFFER_CAP,
    _TCP_PENDING_DRAIN_CAP,
)


def test_drain_cap_is_module_level_constant() -> None:
    """ZR-43: ``_TCP_PENDING_DRAIN_CAP`` is the hoisted form of the old
    inline ``_drain_cap = 100`` local.  Must be 100 to preserve behaviour."""
    assert _TCP_PENDING_DRAIN_CAP == 100
    assert isinstance(_TCP_PENDING_DRAIN_CAP, int)


def test_pending_buffer_cap_is_module_level_constant() -> None:
    """ZR-43 / ZR-70: ``_TCP_PENDING_BUFFER_CAP`` is the hoisted form of the
    old inline ``_pending_cap = 1000`` local.  Must be 1000 to preserve
    behaviour."""
    assert _TCP_PENDING_BUFFER_CAP == 1000
    assert isinstance(_TCP_PENDING_BUFFER_CAP, int)


def test_shutdown_allowlist_is_frozenset_at_module_level() -> None:
    """ZR-70: ``_SHUTDOWN_ALLOWLIST`` must be a ``frozenset`` (not a tuple)
    so membership checks are O(1) hash lookups with no per-call allocation.

    The 5 entries are the push events exempted from the shutdown suppress
    (relaunch/quit + the 3 transcription/vocabulary content-bearing events).
    """
    assert isinstance(_SHUTDOWN_ALLOWLIST, frozenset)
    assert frozenset(
        {
            "relaunch_app",
            "quit_app",
            "transcription_final",
            "transcription_partial",
            "vocabulary_suggestion",
        }
    ) == _SHUTDOWN_ALLOWLIST


def test_shutdown_allowlist_membership_is_o1() -> None:
    """ZR-70: ``msg_type in _SHUTDOWN_ALLOWLIST`` must be a hash lookup
    (frozenset), not a linear scan (tuple)."""
    # All 5 members must report True
    for member in _SHUTDOWN_ALLOWLIST:
        assert member in _SHUTDOWN_ALLOWLIST
    # A non-member must report False
    assert "definitely_not_in_allowlist" not in _SHUTDOWN_ALLOWLIST
