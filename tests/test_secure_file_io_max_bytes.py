"""``_secure_read_text``."""

from __future__ import annotations

import json
import os

import pytest

_DEFAULT_MAX_READ_BYTES = 16 * 1024 * 1024  # mirrors secure_file_io._DEFAULT_MAX_READ_BYTES


class TestSecureReadTextMaxBytesDefault:
    """FR-53: ``_secure_read_text`` defaults to a 16 MiB cap."""

    def test_default_cap_rejects_oversized_file(self, tmp_path):
        """A file larger than the default 16 MiB cap must raise"""
        from voice_typer.server.secure_file_io import _secure_read_text

        big_file = tmp_path / "big.txt"
        # Write 20 MiB of ASCII 'a' (1 byte per char → 20 MiB on disk).
        chunk = "a" * (1024 * 1024)  # 1 MiB
        with open(big_file, "w", encoding="utf-8") as f:
            for _ in range(20):
                f.write(chunk)
        assert big_file.stat().st_size == 20 * 1024 * 1024

        with pytest.raises(ValueError, match="max_bytes"):
            _secure_read_text(big_file)

    def test_default_cap_accepts_normal_file(self, tmp_path):
        """A normal-sized file (< 16 MiB) must read successfully"""
        from voice_typer.server.secure_file_io import _secure_read_text

        normal_file = tmp_path / "normal.txt"
        normal_file.write_text("hello world")
        assert _secure_read_text(normal_file) == "hello world"


class TestSecureReadTextExplicitMaxBytes:
    """FR-53: callers can pass an explicit ``max_bytes`` to override"""

    def test_explicit_cap_rejects_oversized_file(self, tmp_path):
        """A 1 KiB file with ``max_bytes=512`` must raise."""
        from voice_typer.server.secure_file_io import _secure_read_text

        f = tmp_path / "f.txt"
        f.write_text("x" * 1024)
        with pytest.raises(ValueError, match="max_bytes=512"):
            _secure_read_text(f, max_bytes=512)

    def test_explicit_cap_accepts_at_boundary(self, tmp_path):
        """A 1 KiB file with ``max_bytes=1024`` must succeed (the cap"""
        from voice_typer.server.secure_file_io import _secure_read_text

        f = tmp_path / "f.txt"
        f.write_text("x" * 1024)
        # 1024 bytes is NOT > 1024, so this should succeed.
        assert _secure_read_text(f, max_bytes=1024) == "x" * 1024

    def test_explicit_cap_just_above_boundary_rejects(self, tmp_path):
        """A 1025-byte file with ``max_bytes=1024`` must raise (the"""
        from voice_typer.server.secure_file_io import _secure_read_text

        f = tmp_path / "f.txt"
        f.write_text("x" * 1025)
        with pytest.raises(ValueError, match="max_bytes=1024"):
            _secure_read_text(f, max_bytes=1024)

    def test_max_bytes_none_is_unbounded(self, tmp_path):
        """``max_bytes=None`` disables the cap entirely (legacy"""
        from voice_typer.server.secure_file_io import _secure_read_text

        big_file = tmp_path / "big.txt"
        chunk = "a" * (1024 * 1024)
        with open(big_file, "w", encoding="utf-8") as f:
            for _ in range(20):
                f.write(chunk)
        assert big_file.stat().st_size == 20 * 1024 * 1024

        content = _secure_read_text(big_file, max_bytes=None)
        assert len(content) == 20 * 1024 * 1024


class TestSecureReadTextByteCounting:
    """FR-53: the cap is on BYTES, not CHARACTERS.  For non-ASCII"""

    def test_cjk_content_counted_by_bytes(self, tmp_path):
        """A file of CJK characters (3 bytes per char in UTF-8) must"""
        from voice_typer.server.secure_file_io import _secure_read_text

        # U+4E2D (中) is 3 bytes in UTF-8.
        cjk_char = "\u4e2d"
        f = tmp_path / "cjk.txt"
        content = cjk_char * 100  # 100 chars = 300 bytes
        f.write_text(content, encoding="utf-8")
        assert f.stat().st_size == 300  # sanity: 3 bytes per char

        with pytest.raises(ValueError, match="max_bytes=200"):
            _secure_read_text(f, max_bytes=200)

    def test_cjk_content_under_cap_succeeds(self, tmp_path):
        """100 CJK chars (300 bytes) with ``max_bytes=400`` must"""
        from voice_typer.server.secure_file_io import _secure_read_text

        cjk_char = "\u4e2d"
        f = tmp_path / "cjk.txt"
        content = cjk_char * 100
        f.write_text(content, encoding="utf-8")
        assert _secure_read_text(f, max_bytes=400) == content

    def test_emoji_content_counted_by_bytes(self, tmp_path):
        """Emoji (U+1F600 😀) is 4 bytes in UTF-8.  100 emoji = 400"""
        from voice_typer.server.secure_file_io import _secure_read_text

        emoji = "\U0001f600"
        f = tmp_path / "emoji.txt"
        content = emoji * 100  # 100 chars = 400 bytes
        f.write_text(content, encoding="utf-8")
        assert f.stat().st_size == 400

        with pytest.raises(ValueError, match="max_bytes=256"):
            _secure_read_text(f, max_bytes=256)


class TestSecureReadTextChunkedAbort:
    """FR-53: when the cap is exceeded, the read must abort IMMEDIATELY"""

    def test_abort_does_not_read_whole_file(self, tmp_path, monkeypatch):
        """Write a 10 MiB file, set ``max_bytes=1 MiB``, and verify"""
        from voice_typer.server import secure_file_io

        big_file = tmp_path / "big.txt"
        chunk = "a" * (1024 * 1024)
        with open(big_file, "w", encoding="utf-8") as f:
            for _ in range(10):
                f.write(chunk)
        assert big_file.stat().st_size == 10 * 1024 * 1024

        read_calls: list[int] = []

        class CountingFileWrapper:
            """Wraps a real file object and counts read calls."""

            def __init__(self, real_file):
                self._real = real_file

            def read(self, n=-1):
                read_calls.append(n)
                return self._real.read(n)

            def __getattr__(self, name):
                return getattr(self._real, name)

            def fileno(self):
                return self._real.fileno()

            def close(self):
                return self._real.close()

            # The Windows branch of _secure_read_text reads via
            def __enter__(self):
                return self

            def __exit__(self, *exc_info):
                return self._real.__exit__(*exc_info)

        import builtins

        real_fdopen = os.fdopen
        real_open = builtins.open

        def counting_fdopen(fd, *args, **kwargs):
            real_f = real_fdopen(fd, *args, **kwargs)
            return CountingFileWrapper(real_f)

        def counting_open(file, *args, **kwargs):
            real_f = real_open(file, *args, **kwargs)
            return CountingFileWrapper(real_f)

        monkeypatch.setattr(os, "fdopen", counting_fdopen)
        monkeypatch.setattr(builtins, "open", counting_open)

        with pytest.raises(ValueError, match="max_bytes"):
            secure_file_io._secure_read_text(big_file, max_bytes=1024 * 1024)

        # The read must abort after at most ~17 chunks (1 MiB / 64 KiB
        assert len(read_calls) < 100, (
            f"FR-53 regression: read() was called {len(read_calls)} "
            f"times for a 10 MiB file with max_bytes=1 MiB. The "
            f"chunked abort should have fired after ~17 chunks (1 MiB "
            f"/ 64 KiB), but the helper read the whole file. "
            f"Pre-fix this was a single f.read() with no chunking, "
            f"the whole 10 MiB was loaded into RAM before the cap "
            f"check could fire."
        )
        # The lower bound depends on the platform branch:
        if os.name == "nt":
            assert len(read_calls) == 0, (
                "FR-53: the Windows size pre-check must reject the "
                f"oversized file before any read(); got {len(read_calls)} "
                f"read calls"
            )
        else:
            assert len(read_calls) >= 16, (
                f"FR-53 regression: read() was called only {len(read_calls)} "
                f"times, expected at least 16 chunks to exceed the 1 MiB "
                f"cap. The helper may be passing too large a chunk size to "
                f"read(), which would defeat the chunked-abort purpose."
            )


class TestPersistedJSONLoadRespectsMaxBytes:
    """enforces the 16 MiB cap.  A >16 MiB file at the path triggers"""

    def test_oversized_file_quarantined_and_default_returned(self, tmp_path):
        """A >16 MiB file at the PersistedJSON path must be"""
        from voice_typer.server.secure_file_io import PersistedJSON

        path = tmp_path / "state.json"
        # Write 20 MiB of junk (exceeds the 16 MiB default cap).
        chunk = "x" * (1024 * 1024)
        with open(path, "w", encoding="utf-8") as f:
            for _ in range(20):
                f.write(chunk)
        assert path.stat().st_size == 20 * 1024 * 1024

        sentinel = {"sentinel": "default-value"}
        pj = PersistedJSON(path, default=sentinel)
        result = pj.load()

        # The default must be returned (ValueError from the cap is
        assert result == sentinel

        assert not path.exists(), (
            "FR-53 regression: the oversized file was NOT quarantined "
            "— it should have been renamed to .corrupt-<ts> by the "
            "load-failure handler."
        )
        corrupt_files = list(tmp_path.glob("state.json.corrupt-*"))
        assert len(corrupt_files) == 1, (
            f"FR-53 regression: expected exactly 1 .corrupt-* file, "
            f"got {len(corrupt_files)}: {[f.name for f in corrupt_files]}"
        )

    def test_normal_file_loads_correctly(self, tmp_path):
        """A normal-sized JSON file must load correctly under the"""
        from voice_typer.server.secure_file_io import PersistedJSON

        path = tmp_path / "state.json"
        data = {"key": "value", "nested": {"a": 1, "b": [2, 3]}}
        path.write_text(json.dumps(data), encoding="utf-8")

        pj = PersistedJSON(path, default=None)
        assert pj.load() == data
