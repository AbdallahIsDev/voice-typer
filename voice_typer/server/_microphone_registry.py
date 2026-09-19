"""Microphone id registry helpers."""

from __future__ import annotations

import builtins
from collections.abc import Iterable, Iterator


class MicrophoneRegistry:
    """Owns the cached list of available microphones."""

    def __init__(self) -> None:
        self._items: list[dict[str, object]] = []

    def list(self) -> builtins.list[dict[str, object]]:
        """Return a shallow copy of the cached microphone list.

        Returns a COPY (not the internal list) so callers can iterate
        """
        return list(self._items)

    def add(self, mic: dict[str, object]) -> None:
        """Append a single microphone descriptor to the cache."""
        self._items.append(mic)

    def extend(self, mics: Iterable[dict[str, object]]) -> None:
        """Append multiple microphone descriptors to the cache."""
        self._items.extend(mics)

    def replace(self, mics: Iterable[dict[str, object]]) -> None:
        """Atomically replace the entire cache with ``mics``."""
        self._items = list(mics)

    def clear(self) -> None:
        """Empty the cache."""
        self._items.clear()

    def __iter__(self) -> Iterator[dict[str, object]]:
        """Iterate the cached microphones (live view: see :meth:`list` for a snapshot)."""
        return iter(self._items)

    def __len__(self) -> int:
        """Return the number of cached microphones."""
        return len(self._items)
