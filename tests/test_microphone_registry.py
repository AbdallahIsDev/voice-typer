"""Tests for :class:`voice_typer.server._microphone_registry.MicrophoneRegistry`.

AC-66: extract MicrophoneRegistry that owns the legacy ``_microphones``
list and exposes intent-revealing methods (``list`` / ``add`` /
``extend`` / ``replace`` / ``clear`` + ``__iter__`` / ``__len__``).
"""

from __future__ import annotations

from voice_typer.server._microphone_registry import MicrophoneRegistry


def _mic(idx: int, name: str = "") -> dict:
    """Build a minimal microphone descriptor dict for tests."""
    return {"index": idx, "id": str(idx), "name": name or f"mic{idx}"}


class TestMicrophoneRegistryInitialState:
    """A freshly-constructed registry is empty."""

    def test_fresh_registry_is_empty(self) -> None:
        mr = MicrophoneRegistry()
        assert len(mr) == 0
        assert mr.list() == []
        assert list(mr) == []


class TestMicrophoneRegistryAdd:
    """``add`` appends a single microphone descriptor."""

    def test_add_one_microphone(self) -> None:
        mr = MicrophoneRegistry()
        m = _mic(0, "USB Mic")
        mr.add(m)
        assert len(mr) == 1
        assert mr.list() == [m]

    def test_add_multiple_microphones_preserves_order(self) -> None:
        mr = MicrophoneRegistry()
        m0, m1, m2 = _mic(0), _mic(1), _mic(2)
        mr.add(m0)
        mr.add(m1)
        mr.add(m2)
        assert mr.list() == [m0, m1, m2]


class TestMicrophoneRegistryExtend:
    """``extend`` appends multiple descriptors in one call."""

    def test_extend_adds_multiple_at_once(self) -> None:
        mr = MicrophoneRegistry()
        mics = [_mic(0), _mic(1), _mic(2)]
        mr.extend(mics)
        assert mr.list() == mics

    def test_extend_on_empty_registry(self) -> None:
        mr = MicrophoneRegistry()
        mr.extend([])
        assert mr.list() == []

    def test_extend_appends_to_existing_items(self) -> None:
        mr = MicrophoneRegistry()
        mr.add(_mic(0))
        mr.extend([_mic(1), _mic(2)])
        assert mr.list() == [_mic(0), _mic(1), _mic(2)]


class TestMicrophoneRegistryReplace:
    """``replace`` atomically swaps the entire cache."""

    def test_replace_overwrites_existing_items(self) -> None:
        mr = MicrophoneRegistry()
        mr.add(_mic(0))
        mr.add(_mic(1))
        new_mics = [_mic(10), _mic(11), _mic(12)]
        mr.replace(new_mics)
        assert mr.list() == new_mics

    def test_replace_with_empty_list_clears_registry(self) -> None:
        mr = MicrophoneRegistry()
        mr.add(_mic(0))
        mr.replace([])
        assert mr.list() == []
        assert len(mr) == 0

    def test_replace_with_iterable_consumes_lazily(self) -> None:
        """``replace`` accepts any iterable (not just list)."""
        mr = MicrophoneRegistry()

        def _gen():
            yield _mic(0)
            yield _mic(1)

        mr.replace(_gen())
        assert len(mr) == 2


class TestMicrophoneRegistryClear:
    """``clear`` empties the registry."""

    def test_clear_empties_non_empty_registry(self) -> None:
        mr = MicrophoneRegistry()
        mr.add(_mic(0))
        mr.add(_mic(1))
        mr.clear()
        assert mr.list() == []
        assert len(mr) == 0

    def test_clear_on_empty_registry_is_a_noop(self) -> None:
        mr = MicrophoneRegistry()
        mr.clear()
        assert mr.list() == []


class TestMicrophoneRegistryListReturnsCopy:
    """``list`` returns a SHALLOW COPY (not the internal list)."""

    def test_list_returns_a_copy(self) -> None:
        mr = MicrophoneRegistry()
        m = _mic(0)
        mr.add(m)
        snapshot = mr.list()
        # Mutating the snapshot must not affect the registry.
        snapshot.append(_mic(99))
        assert mr.list() == [m]
        assert len(mr) == 1

    def test_list_with_empty_registry_returns_new_empty_list_each_time(self) -> None:
        mr = MicrophoneRegistry()
        s1 = mr.list()
        s2 = mr.list()
        assert s1 == s2 == []
        assert s1 is not s2, "list() must return a fresh copy each call"


class TestMicrophoneRegistryIteration:
    """``__iter__`` and ``__len__`` support ergonomic read access."""

    def test_iter_yields_all_items(self) -> None:
        mr = MicrophoneRegistry()
        m0, m1, m2 = _mic(0), _mic(1), _mic(2)
        mr.add(m0)
        mr.add(m1)
        mr.add(m2)
        items = list(mr)
        assert items == [m0, m1, m2]

    def test_len_matches_item_count(self) -> None:
        mr = MicrophoneRegistry()
        assert len(mr) == 0
        for i in range(5):
            mr.add(_mic(i))
        assert len(mr) == 5

    def test_iter_is_a_live_view(self) -> None:
        """``__iter__`` yields the internal items (live view).

        Callers that need a consistent snapshot should use ``list()``
        (which returns a copy) — direct iteration may see concurrent
        mutation if another thread is modifying the registry.
        """
        mr = MicrophoneRegistry()
        mr.add(_mic(0))
        iterator = iter(mr)
        mr.add(_mic(1))
        items = list(iterator)
        # The iterator sees the post-add state (live view).
        assert len(items) == 2
