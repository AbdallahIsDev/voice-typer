"""MicrophoneRegistry — owns the cached list of available microphones.

Pre-refactor: ``VoiceTyperApp.__init__`` declared::

    self._microphones: list[dict] = []

and three external modules reached into it directly via
``getattr(app, "_microphones")`` or ``app._microphones = mics``:
``service/microphone_test.py`` (3 call sites) and
``startup_tasks.py`` (2 call sites) — plus a number of test
modules. The private attribute was a backdoor API surface that
blocked safe rename / move and forced every consumer to know
the cache was a plain ``list``.

This module introduces an explicit :class:`MicrophoneRegistry`
that owns the cached microphone list and exposes intent-revealing
methods: :meth:`list`, :meth:`add`, :meth:`clear`, plus
:meth:`__iter__` / :meth:`__len__` for ergonomic read access.

Back-compat: ``VoiceTyperApp._microphones`` remains accessible (as
a read-write property delegating to this registry) so non-owned
consumer files (e.g. ``tray_menu.py:794`` which uses
``getattr(controller, "_microphones", None)``) keep working
unchanged. Only the owned consumer files
(``service/microphone_test.py``, ``startup_tasks.py``) are
migrated to the new API in this wave.
"""

from __future__ import annotations

import builtins
from collections.abc import Iterable, Iterator


class MicrophoneRegistry:
    """Owns the cached list of available microphones.

    The registry is a thin wrapper around a ``list[dict]`` — each
    entry is a microphone descriptor dict (``{"index": int, "name":
    str, ...}``) returned by ``list_microphones()`` from
    :mod:`voice_typer.server.server_platform`. The cache is
    populated by ``startup_tasks.load_microphones`` on a background
    thread at startup, and refreshed on demand by
    ``MicrophoneTestMixin.refresh_microphones``.

    Thread safety: the underlying list operations (``append``,
    ``clear``, iteration) are individually atomic under CPython's
    GIL — same guarantee the legacy bare ``list`` had. Callers that
    need a consistent snapshot should use :meth:`list` (which
    returns a shallow copy) rather than iterating the registry
    directly while another thread may be mutating it.
    """

    def __init__(self) -> None:
        self._items: list[dict[str, object]] = []

    # ── Public API ─────────────────────────────────────────────────

    def list(self) -> builtins.list[dict[str, object]]:
        """Return a shallow copy of the cached microphone list.

        Returns a COPY (not the internal list) so callers can iterate
        or mutate the result without affecting the cache — matches
        the safety contract that the legacy ``app._microphones``
        callers implicitly relied on (they always treated the value
        as a snapshot, never mutated in place).
        """
        return list(self._items)

    def add(self, mic: dict[str, object]) -> None:
        """Append a single microphone descriptor to the cache."""
        self._items.append(mic)

    def extend(self, mics: Iterable[dict[str, object]]) -> None:
        """Append multiple microphone descriptors to the cache.

        Bulk-add variant for ``startup_tasks.load_microphones`` (which
        replaces the entire cache with a freshly-queried list).
        Equivalent to ``for m in mics: self.add(m)`` but a single
        C-level extend call.
        """
        self._items.extend(mics)

    def replace(self, mics: Iterable[dict[str, object]]) -> None:
        """Atomically replace the entire cache with ``mics``.

        Used by ``load_microphones`` and ``refresh_microphones`` to
        refresh the cache in one shot — the legacy code did
        ``app._microphones = mics`` (a fresh list rebinding), which
        is racy if another thread is mid-iteration on the OLD list.
        This method clears + extends under a single rebind so the
        replacement is observable as one atomic state transition.
        """
        self._items = list(mics)

    def clear(self) -> None:
        """Empty the cache."""
        self._items.clear()

    # ── Convenience dunder access ──────────────────────────────────

    def __iter__(self) -> Iterator[dict[str, object]]:
        """Iterate the cached microphones (live view — see :meth:`list` for a snapshot)."""
        return iter(self._items)

    def __len__(self) -> int:
        """Return the number of cached microphones."""
        return len(self._items)
