"""Targeted test for ZR-43 + ZR-70 module-level constant hoist."""

from __future__ import annotations

from voice_typer.server.ipc_server import (
    _SHUTDOWN_ALLOWLIST,
    _TCP_PENDING_BUFFER_CAP,
    _TCP_PENDING_DRAIN_CAP,
)


def test_drain_cap_is_module_level_constant() -> None:
    """``_TCP_PENDING_DRAIN_CAP`` is the hoisted form of the old"""
    assert _TCP_PENDING_DRAIN_CAP == 100
    assert isinstance(_TCP_PENDING_DRAIN_CAP, int)


def test_pending_buffer_cap_is_module_level_constant() -> None:
    """old inline ``_pending_cap = 1000`` local.  Must be 1000 to preserve"""
    assert _TCP_PENDING_BUFFER_CAP == 1000
    assert isinstance(_TCP_PENDING_BUFFER_CAP, int)


def test_shutdown_allowlist_is_frozenset_at_module_level() -> None:
    """``_SHUTDOWN_ALLOWLIST`` must be a ``frozenset`` (not a tuple)"""
    assert isinstance(_SHUTDOWN_ALLOWLIST, frozenset)
    assert (
        frozenset(
            {
                "relaunch_app",
                "quit_app",
                "transcription_final",
                "transcription_partial",
                "vocabulary_suggestion",
            }
        )
        == _SHUTDOWN_ALLOWLIST
    )


def test_shutdown_allowlist_membership_is_o1() -> None:
    """``msg_type in _SHUTDOWN_ALLOWLIST`` must be a hash lookup"""
    # All 5 members must report True
    for member in _SHUTDOWN_ALLOWLIST:
        assert member in _SHUTDOWN_ALLOWLIST
    # A non-member must report False
    assert "definitely_not_in_allowlist" not in _SHUTDOWN_ALLOWLIST
