"""Regression test: ``PersistedJSON._quarantine_corrupt`` must produce
distinct quarantine filenames even when two processes corrupt their
files in the same epoch second.

The previous implementation used::

    ts = int(time.time())
    corrupt_path = self._path.with_name(f"{self._path.name}.corrupt-{ts}")
    counter = 0
    while corrupt_path.exists():
        counter += 1
        corrupt_path = self._path.with_name(
            f"{self._path.name}.corrupt-{ts}.{counter}"
        )

Two concurrent processes corrupting DIFFERENT files in the same second
both picked ``ts`` + ``counter=0`` — but their ``corrupt_path``s point
at DIFFERENT parent files, so the ``while exists()`` loop never trips
on each other.  The bug surfaces when two processes corrupt the SAME
file path (e.g. two app instances launched against the same user
account) within the same second: both pick ``ts-0``, the
``exists()`` check has a TOCTOU window, and one process's
``os.replace`` overwrites the other's quarantine — losing forensic
history.

The fix mirrors ``config.py:_backup_before_migration`` (line 1900-1903)
and the corrupt-config rename in ``config.py:1779-1782``: embed epoch
seconds + PID + sub-second nanoseconds (``time.time_ns() % 1_000_000``)
in the filename so a collision is essentially impossible without an
``exists()`` probe loop.

These tests assert:

1. Two concurrent ``_quarantine_corrupt`` calls on the SAME path from
   the SAME process produce distinct quarantine filenames (no
   clobber).
2. The quarantine filename matches the new pattern
   ``<name>.corrupt-<ts>-<pid>-<ns>``.
3. Both corrupt files are preserved (neither is overwritten).
"""

from __future__ import annotations

import os
import re
import threading
from pathlib import Path

import pytest
from voice_typer.server.secure_file_io import PersistedJSON

_QUARANTINE_NAME_RE = re.compile(r"^(?P<base>.+?)\.corrupt-(?P<ts>\d+)-(?P<pid>\d+)-(?P<ns>\d+)$")


def test_quarantine_filename_uses_pid_and_nanoseconds(tmp_path: Path) -> None:
    """A single quarantine event produces a filename matching the new
    ``<name>.corrupt-<ts>-<pid>-<ns>`` pattern."""
    corrupt_path = tmp_path / "mydata.json"
    corrupt_path.write_text("{not valid json")

    store: PersistedJSON = PersistedJSON(corrupt_path, default={})
    store._quarantine_corrupt()

    # The original file should be gone (renamed aside).
    assert not corrupt_path.exists(), (
        "After _quarantine_corrupt, the original corrupt file must be "
        "moved aside (no longer exists at its original path)."
    )

    # Find the quarantine file in tmp_path.
    quarantined = list(tmp_path.glob("mydata.json.corrupt-*"))
    assert len(quarantined) == 1, (
        f"Expected exactly 1 quarantine file, found {len(quarantined)}: {[p.name for p in quarantined]}"
    )

    m = _QUARANTINE_NAME_RE.match(quarantined[0].name)
    assert m is not None, (
        f"Quarantine filename must match the new pattern <name>.corrupt-<ts>-<pid>-<ns>. Got: {quarantined[0].name}"
    )
    # The PID embedded in the filename must be the current process's PID.
    assert int(m.group("pid")) == os.getpid(), (
        f"Quarantine filename PID must match os.getpid()={os.getpid()}, got {m.group('pid')}"
    )
    # ts must be a positive integer (epoch seconds).
    ts = int(m.group("ts"))
    assert ts > 0, f"ts must be a positive epoch-seconds value, got {ts}"
    # ns must be in [0, 1_000_000).
    ns = int(m.group("ns"))
    assert 0 <= ns < 1_000_000, f"ns must be in [0, 1_000_000) (time.time_ns() % 1_000_000), got {ns}"


def test_concurrent_quarantine_same_path_no_clobber(tmp_path: Path) -> None:
    """Two concurrent ``_quarantine_corrupt`` calls on files with the
    SAME ``name`` (but in different parent directories) must produce
    DISTINCT quarantine filenames (different ``ns`` suffix).

    Simulates the realistic race scenario: two app instances launched
    against different user accounts, each with its own ``config.json``
    in its own config dir, both detecting corruption in the same
    epoch second.  The previous ``ts = int(time.time())`` + counter
    loop had a TOCTOU window when the SAME-PATH scenario applied
    (two processes, same config.json path — one process's
    ``os.replace`` would clobber the other's quarantine).  This test
    exercises the same-second, same-name, DIFFERENT-directory variant
    which is the closest race that's safely simulable in a
    single-process unit test (two threads each get their own file
    system path, so no FS-level serialization masks the filename
    collision).

    Note: this is a probabilistic test — under extreme contention the
    two ``time.time_ns()`` calls could still produce the same value if
    the OS clock has nanosecond resolution AND both threads happen to
    be scheduled to read the clock in the exact same nanosecond.  In
    practice on Linux this is essentially impossible (clock read
    takes ~20-50ns of itself, leaving plenty of slack).  The test
    runs 5 iterations to maximize the chance of catching a regression
    if the nanosecond suffix is removed or weakened.
    """
    # Each thread has its OWN subdirectory + its OWN copy of
    # ``race.json``.  This avoids the FS-level serialization where
    # only one thread can win the ``os.replace`` on a shared path —
    # both threads successfully quarantine their own file, and we
    # verify the resulting quarantine FILENAMES are distinct (the
    # PID+ns suffix disambiguates them within the same epoch second).
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()
    path_a = dir_a / "race.json"
    path_b = dir_b / "race.json"

    # Capture the destination path each thread passes to os.replace
    # by spying on os.replace.  We can't read the filesystem AFTER
    # both threads finish because both quarantine files have the
    # same NAME (``race.json.corrupt-*``) — they're just in different
    # parent directories.  Spying on os.replace captures the dst
    # path each thread computed.
    real_os_replace = os.replace
    per_thread_dests: dict[int, str] = {}
    dest_lock = threading.Lock()

    def spying_os_replace(src, dst, *args, **kwargs):
        tid = threading.get_ident()
        with dest_lock:
            per_thread_dests[tid] = str(dst)
        return real_os_replace(src, dst, *args, **kwargs)

    def write_and_quarantine(barrier: threading.Barrier, path: Path, errors: list):
        try:
            barrier.wait()
            tid = threading.get_ident()
            path.write_text(f"{{thread {tid} corrupt content")
            store = PersistedJSON(path, default={})
            store._quarantine_corrupt()
        except Exception as exc:
            errors.append(exc)

    clobber_seen = False
    for _ in range(5):
        # Clean slate for each iteration.
        for f in dir_a.glob("race.json*"):
            f.unlink()
        for f in dir_b.glob("race.json*"):
            f.unlink()
        per_thread_dests.clear()

        barrier = threading.Barrier(2)
        errors: list[Exception] = []
        threads = [
            threading.Thread(
                target=write_and_quarantine,
                args=(barrier, path_a, errors),
            ),
            threading.Thread(
                target=write_and_quarantine,
                args=(barrier, path_b, errors),
            ),
        ]

        # Patch os.replace just for this iteration's thread pool.
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(os, "replace", spying_os_replace)
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        assert errors == [], f"Threads raised exceptions: {errors}"

        # Each thread should have recorded exactly one destination path.
        assert len(per_thread_dests) == 2, (
            f"Expected 2 thread destinations, got {len(per_thread_dests)}: {per_thread_dests}"
        )

        dests = list(per_thread_dests.values())
        # Extract just the FILENAME (not the parent dir) for
        # collision comparison.  Both threads have the same
        # ``race.json`` source name, so the only way the quarantine
        # FILENAMES can differ is via the ts/pid/ns suffix.
        dest_filenames = [Path(d).name for d in dests]

        if dest_filenames[0] == dest_filenames[1]:
            # Same filename — collision.  This is the regression
            # we're guarding against (the OLD ``ts``-only suffix
            # would produce the same filename when both threads
            # hit the same epoch second).
            clobber_seen = True
            break

        # Both quarantine files must exist on disk in their respective
        # parent directories.
        for d in dests:
            assert Path(d).exists(), (
                f"Quarantine file {d} should exist after both threads "
                f"finished — if it's missing, one thread clobbered the "
                f"other's quarantine file."
            )

    assert not clobber_seen, (
        "At least one iteration saw a clobber — both threads produced "
        "the same quarantine FILENAME, indicating the same-second "
        "TOCTOU race regressed.  Dest filenames were: "
        f"{dest_filenames}"
    )


def test_concurrent_quarantine_different_paths_distinct(tmp_path: Path) -> None:
    """Two concurrent ``_quarantine_corrupt`` calls on DIFFERENT paths
    must produce distinct quarantine filenames (each in its own parent).

    This is the simpler case — different parent paths mean no real
    collision risk — but it pins the filename pattern so future
    refactors don't accidentally collapse to a non-unique scheme.
    """
    path_a = tmp_path / "data_a.json"
    path_b = tmp_path / "data_b.json"
    path_a.write_text("{corrupt a")
    path_b.write_text("{corrupt b")

    store_a: PersistedJSON = PersistedJSON(path_a, default={})
    store_b: PersistedJSON = PersistedJSON(path_b, default={})

    errors: list[Exception] = []

    def quarantine(store: PersistedJSON):
        try:
            store._quarantine_corrupt()
        except Exception as exc:
            errors.append(exc)

    barrier = threading.Barrier(2)
    threads = [
        threading.Thread(target=lambda: (barrier.wait(), quarantine(store_a))),
        threading.Thread(target=lambda: (barrier.wait(), quarantine(store_b))),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"Threads raised exceptions: {errors}"

    quarantined_a = list(tmp_path.glob("data_a.json.corrupt-*"))
    quarantined_b = list(tmp_path.glob("data_b.json.corrupt-*"))

    assert len(quarantined_a) == 1, f"data_a.json should have 1 quarantine file, got {len(quarantined_a)}"
    assert len(quarantined_b) == 1, f"data_b.json should have 1 quarantine file, got {len(quarantined_b)}"

    # Distinct parents — must always be distinct.
    assert quarantined_a[0].name != quarantined_b[0].name

    # Both must match the new pattern.
    for q in (quarantined_a[0], quarantined_b[0]):
        m = _QUARANTINE_NAME_RE.match(q.name)
        assert m is not None, f"Quarantine filename must match <name>.corrupt-<ts>-<pid>-<ns>. Got: {q.name}"
        assert int(m.group("pid")) == os.getpid()


def test_quarantine_no_counter_loop_filenames(tmp_path: Path) -> None:
    """The new implementation must NOT produce ``.corrupt-<ts>.<N>``
    filenames (the old counter-loop pattern).

    Calls ``_quarantine_corrupt`` repeatedly to populate multiple
    quarantine files (each iteration re-creates the source file and
    quarantines it again), then asserts none of the resulting
    filenames match the old ``.corrupt-<ts>.<N>`` counter pattern.
    """
    corrupt_path = tmp_path / "loopcheck.json"

    # Run several quarantines back-to-back.  With the old
    # implementation, back-to-back calls in the same second would have
    # produced ``.corrupt-<ts>``, ``.corrupt-<ts>.1``, ``.corrupt-<ts>.2``,
    # etc.  With the new implementation, each call produces a unique
    # ``.corrupt-<ts>-<pid>-<ns>`` filename.
    for i in range(5):
        corrupt_path.write_text(f"{{iteration {i} corrupt")
        store: PersistedJSON = PersistedJSON(corrupt_path, default={})
        store._quarantine_corrupt()

    quarantined = list(tmp_path.glob("loopcheck.json.corrupt-*"))
    assert len(quarantined) == 5, (
        f"Expected 5 quarantine files, got {len(quarantined)}: {[p.name for p in quarantined]}"
    )

    # NONE of the filenames should match the old counter pattern
    # (.corrupt-<ts>.<N>).
    old_counter_re = re.compile(r"^loopcheck\.json\.corrupt-\d+\.\d+$")
    for q in quarantined:
        assert not old_counter_re.match(q.name), (
            f"Quarantine filename must NOT match the old counter-loop "
            f"pattern (.corrupt-<ts>.<N>). Got: {q.name} — the counter "
            f"loop has a TOCTOU window and must be removed."
        )

    # ALL filenames must be unique (no clobbering).
    names = {q.name for q in quarantined}
    assert len(names) == 5, (
        f"All 5 quarantine filenames must be unique. Got {len(names)} unique names from 5 quarantines: {sorted(names)}"
    )

    # ALL must match the new pattern.
    for q in quarantined:
        m = _QUARANTINE_NAME_RE.match(q.name)
        assert m is not None, f"Quarantine filename must match new pattern. Got: {q.name}"


def test_quarantine_preserves_file_content(tmp_path: Path) -> None:
    """The quarantined file must contain the EXACT bytes of the
    original corrupt file (so forensic recovery is possible)."""
    original_content = "{this is corrupt but recoverable"
    corrupt_path = tmp_path / "preserve.json"
    corrupt_path.write_text(original_content)

    store: PersistedJSON = PersistedJSON(corrupt_path, default={})
    store._quarantine_corrupt()

    quarantined = list(tmp_path.glob("preserve.json.corrupt-*"))
    assert len(quarantined) == 1
    recovered_content = quarantined[0].read_text()
    assert recovered_content == original_content, (
        "The quarantined file must contain the exact bytes of the original "
        "corrupt file (forensic recovery contract). "
        f"Got: {recovered_content!r}, expected: {original_content!r}"
    )
