"""Regression test: ``PersistedJSON._quarantine_corrupt`` must produce"""

from __future__ import annotations

import os
import re
import threading
from pathlib import Path

import pytest
from voice_typer.server.secure_file_io import PersistedJSON

_QUARANTINE_NAME_RE = re.compile(r"^(?P<base>.+?)\.corrupt-(?P<ts>\d+)-(?P<pid>\d+)-(?P<ns>\d+)$")


def test_quarantine_filename_uses_pid_and_nanoseconds(tmp_path: Path) -> None:
    """A single quarantine event produces a filename matching the new"""
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
    ts = int(m.group("ts"))
    assert ts > 0, f"ts must be a positive epoch-seconds value, got {ts}"
    ns = int(m.group("ns"))
    assert 0 <= ns < 1_000_000, f"ns must be in [0, 1_000_000) (time.time_ns() % 1_000_000), got {ns}"


def test_concurrent_quarantine_same_path_no_clobber(tmp_path: Path) -> None:
    """SAME ``name`` (but in different parent directories) must produce"""
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()
    path_a = dir_a / "race.json"
    path_b = dir_b / "race.json"

    # Capture the destination path each thread passes to os.replace
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
        dest_filenames = [Path(d).name for d in dests]

        if dest_filenames[0] == dest_filenames[1]:
            # Same filename, collision.  This is the regression
            clobber_seen = True
            break

        # Both quarantine files must exist on disk in their respective
        for d in dests:
            assert Path(d).exists(), (
                f"Quarantine file {d} should exist after both threads "
                f"finished, if it's missing, one thread clobbered the "
                f"other's quarantine file."
            )

    assert not clobber_seen, (
        "At least one iteration saw a clobber, both threads produced "
        "the same quarantine FILENAME, indicating the same-second "
        "TOCTOU race regressed.  Dest filenames were: "
        f"{dest_filenames}"
    )


def test_concurrent_quarantine_different_paths_distinct(tmp_path: Path) -> None:
    """Two concurrent ``_quarantine_corrupt`` calls on DIFFERENT paths"""
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

    # Distinct parents, must always be distinct.
    assert quarantined_a[0].name != quarantined_b[0].name

    # Both must match the new pattern.
    for q in (quarantined_a[0], quarantined_b[0]):
        m = _QUARANTINE_NAME_RE.match(q.name)
        assert m is not None, f"Quarantine filename must match <name>.corrupt-<ts>-<pid>-<ns>. Got: {q.name}"
        assert int(m.group("pid")) == os.getpid()


def test_quarantine_no_counter_loop_filenames(tmp_path: Path) -> None:
    """The new implementation must NOT produce ``.corrupt-<ts>.<N>``"""
    corrupt_path = tmp_path / "loopcheck.json"

    # Run several quarantines back-to-back.  With the old
    for i in range(5):
        corrupt_path.write_text(f"{{iteration {i} corrupt")
        store: PersistedJSON = PersistedJSON(corrupt_path, default={})
        store._quarantine_corrupt()

    quarantined = list(tmp_path.glob("loopcheck.json.corrupt-*"))
    assert len(quarantined) == 5, (
        f"Expected 5 quarantine files, got {len(quarantined)}: {[p.name for p in quarantined]}"
    )

    # (.corrupt-<ts>.<N>).
    old_counter_re = re.compile(r"^loopcheck\.json\.corrupt-\d+\.\d+$")
    for q in quarantined:
        assert not old_counter_re.match(q.name), (
            f"Quarantine filename must NOT match the old counter-loop "
            f"pattern (.corrupt-<ts>.<N>). Got: {q.name}, the counter "
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
    """The quarantined file must contain the EXACT bytes of the"""
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
