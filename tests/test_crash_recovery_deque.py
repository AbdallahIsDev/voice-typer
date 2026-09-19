"""TY-32 regression test: ``CrashRecovery._entries`` is a bounded deque."""

import collections
import json

import pytest


@pytest.fixture(autouse=True)
def _mock_recovery_owner_acl(monkeypatch):
    """Never run real icacls during crash-recovery tests on Windows hosts."""
    from unittest.mock import MagicMock

    monkeypatch.setattr(
        "voice_typer.server.config._enforce_windows_owner_only_acl",
        MagicMock(return_value=True),
    )


@pytest.fixture
def recovery_dir(tmp_config_dir):
    """Point config to a temp directory (via the canonical tmp_config_dir fixture)."""
    return tmp_config_dir


@pytest.fixture
def cr(recovery_dir):
    """Create a CrashRecovery instance with temp dir."""
    from voice_typer.server.crash_recovery import CrashRecovery

    return CrashRecovery(config_dir=recovery_dir)


def _max_entries():
    """Read MAX_RECOVERY_ENTRIES from the module under test."""
    from voice_typer.server import crash_recovery

    return crash_recovery.MAX_RECOVERY_ENTRIES


class TestEntriesIsBoundedDeque:
    def test_entries_is_deque(self, cr):
        """``_entries`` is a ``collections.deque`` (not a ``list``)."""
        assert isinstance(cr._entries, collections.deque)

    def test_entries_maxlen_matches_constant(self, cr):
        """The deque's ``maxlen`` equals ``MAX_RECOVERY_ENTRIES``."""
        assert cr._entries.maxlen == _max_entries()

    def test_entries_maxlen_is_10(self, cr):
        """Defense-in-depth: the bound is still 10 (the documented value)."""
        assert cr._entries.maxlen == 10
        assert _max_entries() == 10


class TestAutoEviction:
    def test_append_past_max_evicts_oldest(self, cr):
        """Appending past ``maxlen`` drops the OLDEST entry, keeps newest."""
        max_n = _max_entries()
        for i in range(max_n + 5):
            cr.add(f"entry-{i}", pasted=False)
        # Count is capped at max_n, same as the previous ``while`` loop.
        assert cr.count == max_n
        # The newest max_n entries are retained (entry-5 .. entry-14).
        entries = cr.get_all()
        assert [e["text"] for e in entries] == [f"entry-{i}" for i in range(5, max_n + 5)]

    def test_append_exactly_max_does_not_evict(self, cr):
        """Appending exactly ``maxlen`` entries evicts nothing."""
        max_n = _max_entries()
        for i in range(max_n):
            cr.add(f"entry-{i}", pasted=False)
        assert cr.count == max_n
        entries = cr.get_all()
        assert [e["text"] for e in entries] == [f"entry-{i}" for i in range(max_n)]

    def test_eviction_is_oldest_first(self, cr):
        """Repeated appends evict in FIFO order (oldest first)."""
        cr.add("first", pasted=False)
        cr.add("second", pasted=False)
        max_n = _max_entries()
        # Fill the rest with junk to force ``first`` out.
        for i in range(max_n):
            cr.add(f"junk-{i}", pasted=False)
        entries = cr.get_all()
        texts = [e["text"] for e in entries]
        assert "first" not in texts
        assert "second" not in texts


class TestDequeApiCompatibility:
    def test_clear_empties_deque(self, cr):
        """``clear()`` works on the deque (deque.clear() exists)."""
        cr.add("a", pasted=False)
        cr.add("b", pasted=False)
        assert cr.count == 2
        cr.clear()
        assert cr.count == 0
        assert len(cr._entries) == 0

    def test_len_works_on_deque(self, cr):
        """``len(self._entries)`` works on deque (the ``count`` property)."""
        assert cr.count == 0
        cr.add("x", pasted=False)
        assert cr.count == 1
        cr.add("y", pasted=False)
        assert cr.count == 2
        # Internal ``len()`` agrees with the public ``count`` property.
        assert len(cr._entries) == cr.count

    def test_indexing_latest_entry_works(self, cr):
        """``self._entries[-1]`` (used by ``mark_latest_pasted``) works on deque."""
        cr.add("first", pasted=False)
        cr.add("second", pasted=False)
        cr.mark_latest_pasted()
        entries = cr.get_all()
        assert entries[-1]["pasted"] is True
        # The older entry is NOT marked.
        assert entries[0]["pasted"] is False

    def test_indexing_by_position_works(self, cr):
        """``self._entries[index]`` (used by ``mark_pasted``) works on deque."""
        cr.add("a", pasted=False)
        cr.add("b", pasted=False)
        cr.add("c", pasted=False)
        assert cr.mark_pasted(1) is True
        entries = cr.get_all()
        assert entries[1]["pasted"] is True
        assert entries[0]["pasted"] is False
        assert entries[2]["pasted"] is False

    def test_iteration_works(self, cr):
        """``for e in self._entries`` (used by ``get_unpasted``) works on deque."""
        cr.add("pasted-1", pasted=True)
        cr.add("unpasted-1", pasted=False)
        cr.add("pasted-2", pasted=True)
        cr.add("unpasted-2", pasted=False)
        unpasted = cr.get_unpasted()
        texts = [e["text"] for e in unpasted]
        assert texts == ["unpasted-1", "unpasted-2"]

    def test_truthiness_check_works(self, cr):
        """``if self._entries:`` (used by ``__del__``) works on deque."""
        # Empty deque is falsy.
        assert not cr._entries
        cr.add("x", pasted=False)
        # Non-empty deque is truthy.
        assert cr._entries


class TestJsonRoundTrip:
    def test_save_load_preserves_entries(self, recovery_dir):
        """``_save_sync`` writes ``{\"entries\": [...]}`` and ``_load`` reads it back."""
        from voice_typer.server.crash_recovery import CrashRecovery

        cr1 = CrashRecovery(config_dir=recovery_dir)
        cr1.add("alpha", pasted=False)
        cr1.add("beta", pasted=True)
        cr1.add("gamma", pasted=False)
        cr1.flush()
        del cr1

        # Inspect the raw on-disk JSON shape (must still be a list of dicts).
        raw = (recovery_dir / "recovery.json").read_text(encoding="utf-8")
        parsed = json.loads(raw)
        assert isinstance(parsed, dict)
        assert "entries" in parsed
        assert isinstance(parsed["entries"], list)
        assert len(parsed["entries"]) == 3
        assert [e["text"] for e in parsed["entries"]] == [
            "alpha",
            "beta",
            "gamma",
        ]

        # ``_load`` reconstructs a bounded deque from the on-disk list.
        cr2 = CrashRecovery(config_dir=recovery_dir)
        assert cr2.count == 3
        assert isinstance(cr2._entries, collections.deque)
        assert cr2._entries.maxlen == _max_entries()

    def test_load_trims_oversized_disk_state(self, recovery_dir):
        """bound was lowered in a future version), ``_load`` trims to ``maxlen``"""
        from voice_typer.server.crash_recovery import CrashRecovery

        max_n = _max_entries()
        # Write a file with 2x the max entries, simulates a stale file
        oversized = {
            "entries": [
                {"text": f"old-{i}", "timestamp": "2026-01-01T00:00:00", "pasted": False} for i in range(max_n * 2)
            ]
        }
        recovery_path = recovery_dir / "recovery.json"
        recovery_path.parent.mkdir(parents=True, exist_ok=True)
        recovery_path.write_text(json.dumps(oversized), encoding="utf-8")

        cr = CrashRecovery(config_dir=recovery_dir)
        # Trimmed to ``max_n``, the OLDEST entries are dropped (the deque
        assert cr.count == max_n
        entries = cr.get_all()
        assert [e["text"] for e in entries] == [f"old-{i}" for i in range(max_n, max_n * 2)]
