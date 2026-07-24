"""ER-FIX-G2 regression tests.

Covers three fixes from the Group 2 performance/resource review:

* **ER-37** — ``VocabularyManager.apply_to_text`` caches compiled regex
  patterns so ``re.compile`` is called once per phrase per session
  (not per call). Cache is invalidated by every mutation of
  ``self._data``.

* **ER-72** — ``ClipboardManager.paste`` wraps ``Thread().start()`` in
  try/except. On start failure the orphaned ``_pending_restores`` entry
  is removed under the lock so it cannot leak for the process lifetime
  (the entry holds ``self``, the snapshot, and the dictated text).

* **ER-79** — ``credential_store._read_plaintext_fallback`` caches the
  parsed ``config.json`` at module level with an ``st_mtime_ns`` check
  so the 5-per-startup calls don't each re-read + re-parse the same
  file.

These tests are intentionally focused: they verify the caching /
leak-prevention behaviour added by the fix, not the broader
correctness of each module (which is covered by the existing
test_vocabulary*.py / test_clipboard*.py / test_credential_store*.py
suites).
"""

from __future__ import annotations

import json as _json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Mock pynput / pyperclip at import time so the clipboard module loads
# cleanly on a headless Linux box (same pattern as
# test_clipboard_paste_restore.py).
sys.modules.setdefault("pynput", MagicMock())
sys.modules.setdefault("pynput.keyboard", MagicMock())
sys.modules.setdefault("pyperclip", MagicMock())


# ===========================================================================
# ER-37 — VocabularyManager compiled-pattern cache
# ===========================================================================


def _make_vocab(tmp_path) -> "object":
    """Build a VocabularyManager with no bundled corrections.

    ``bundled_path`` points at a non-existent file so the merge starts
    from an empty baseline — the test then controls the exact entry
    count via ``add_phrase`` / ``add_entry``.
    """
    from voice_typer.server.vocabulary import VocabularyManager

    return VocabularyManager(
        config_dir=Path(tmp_path),
        bundled_path=Path(tmp_path) / "nonexistent-bundled.json",
    )


def test_apply_to_text_uses_cached_patterns(tmp_path) -> None:
    """ER-37: ``re.compile`` is called once per phrase per session,
    not once per phrase per call.

    Before the fix, every ``apply_to_text`` call re-compiled every
    phrase-correction pattern. With the cache, the first call compiles
    N patterns and subsequent calls reuse them (0 new compiles).
    """
    import re

    vm = _make_vocab(tmp_path)
    # Three phrase-correction entries.
    vm.add_phrase("phrase_corrections", "foo", "bar")
    vm.add_phrase("phrase_corrections", "hello", "world")
    vm.add_phrase("phrase_corrections", "alpha", "beta")
    # add_phrase invalidates the cache.
    assert vm._compiled_patterns is None

    real_compile = re.compile
    call_count = {"n": 0}

    def counting_compile(*args, **kwargs):
        call_count["n"] += 1
        return real_compile(*args, **kwargs)

    # Patch the public ``re.compile`` symbol. ``apply_to_text`` does
    # ``import re as _re`` then ``_re.compile(...)`` — this lookup hits
    # the patched attribute. ``re.sub`` / ``re.match`` use the internal
    # ``re._compile`` (not ``re.compile``), so they do NOT inflate the
    # counter.
    with patch("re.compile", side_effect=counting_compile):
        vm.apply_to_text("foo hello alpha")
        first_batch = call_count["n"]
        assert first_batch == 3, (
            f"first apply_to_text should compile 3 phrase patterns (one per entry), "
            f"got {first_batch}"
        )
        vm.apply_to_text("foo hello alpha")
        assert call_count["n"] == first_batch, (
            f"second apply_to_text should hit the cache (0 new compiles), "
            f"but count rose to {call_count['n']}"
        )


def test_apply_to_text_cache_rebuilt_after_invalidation(tmp_path) -> None:
    """ER-37: after the cache is invalidated (e.g. by ``add_phrase``),
    the next ``apply_to_text`` rebuilds it — ``re.compile`` is called
    again for every phrase entry."""
    import re

    vm = _make_vocab(tmp_path)
    vm.add_phrase("phrase_corrections", "foo", "bar")
    vm.add_phrase("phrase_corrections", "hello", "world")

    real_compile = re.compile
    call_count = {"n": 0}

    def counting_compile(*args, **kwargs):
        call_count["n"] += 1
        return real_compile(*args, **kwargs)

    with patch("re.compile", side_effect=counting_compile):
        vm.apply_to_text("foo hello")
        assert call_count["n"] == 2
        vm.apply_to_text("foo hello")
        assert call_count["n"] == 2  # cache hit
        # Mutate → invalidate.
        vm.add_phrase("phrase_corrections", "new", "phrase")
        before = call_count["n"]
        vm.apply_to_text("foo hello new")
        assert call_count["n"] == before + 3, (
            f"after invalidation, apply_to_text should recompile all 3 phrase patterns, "
            f"got {call_count['n'] - before} new compiles"
        )


def test_cache_invalidated_on_add_entry(tmp_path) -> None:
    """ER-37: ``add_entry`` (dict-based category mutation) invalidates
    the compiled-pattern cache even though the cache only covers
    phrase-based categories. This is the conservative choice — any
    data mutation busts the cache."""
    vm = _make_vocab(tmp_path)
    vm.apply_to_text("hello")  # build cache
    assert vm._compiled_patterns is not None, "cache should be built after first apply_to_text"
    vm.add_entry("misspellings", "foo", "bar")
    assert vm._compiled_patterns is None, "cache should be invalidated after add_entry"
    vm.apply_to_text("hello")  # rebuild
    assert vm._compiled_patterns is not None, "cache should be rebuilt on next apply_to_text"


def test_cache_invalidated_on_remove_entry(tmp_path) -> None:
    """ER-37: ``remove_entry`` invalidates the cache."""
    vm = _make_vocab(tmp_path)
    vm.add_entry("misspellings", "foo", "bar")
    vm.apply_to_text("hello")
    assert vm._compiled_patterns is not None
    vm.remove_entry("misspellings", "foo")
    assert vm._compiled_patterns is None


def test_cache_invalidated_on_import_json(tmp_path) -> None:
    """ER-37: ``import_json`` invalidates the cache."""
    vm = _make_vocab(tmp_path)
    vm.apply_to_text("hello")
    assert vm._compiled_patterns is not None
    payload = _json.dumps({"phrase_corrections": [["a", "b"]]})
    vm.import_json(payload, merge=True)
    assert vm._compiled_patterns is None


def test_apply_to_text_correctness_preserved(tmp_path) -> None:
    """ER-37: caching does not change the correction output."""
    vm = _make_vocab(tmp_path)
    vm.add_phrase("phrase_corrections", "foo", "bar")
    vm.add_phrase("phrase_corrections", "hello world", "hi earth")
    # Longer phrase wins (sorted longest-first).
    out1 = vm.apply_to_text("foo hello world test")
    out2 = vm.apply_to_text("foo hello world test")
    assert out1 == out2 == "bar hi earth test", f"correction output changed: {out1!r} vs {out2!r}"


# ===========================================================================
# ER-72 — ClipboardManager paste() thread-start leak
# ===========================================================================


@pytest.fixture(autouse=True)
def _mock_display_env(monkeypatch):
    """Ensure DISPLAY is set and WAYLAND_DISPLAY is unset for clipboard tests."""
    monkeypatch.setenv("DISPLAY", ":99")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    yield


def _make_cm():
    """Build a ClipboardManager with mocked keyboard (skip pynput import)."""
    from voice_typer.server import clipboard as clip_mod

    cm = clip_mod.ClipboardManager.__new__(clip_mod.ClipboardManager)
    cm.paste_enabled = True
    cm._keyboard = MagicMock()
    cm._last_paste_time = 0.0  # not rate-limited
    cm._clipboard_seq = 0
    cm._last_copied_text = ""
    cm._clipboard_save_restore_enabled = True
    cm._restore_delay_ms = 10
    return cm


def _drain_pending_restores() -> None:
    from voice_typer.server import clipboard as clip_mod

    with clip_mod._pending_restores_lock:
        clip_mod._pending_restores.clear()


@pytest.fixture(autouse=True)
def _isolate_pending_restores():
    _drain_pending_restores()
    yield
    _drain_pending_restores()


def test_pending_restores_no_leak_when_thread_start_fails() -> None:
    """ER-72: when ``Thread().start()`` raises, the entry appended to
    ``_pending_restores`` is removed under the lock — no leak.

    Before the fix, ``paste()`` appended the entry BEFORE calling
    ``.start()`` and did not catch the failure. A failed start (out of
    thread resources / fd exhaustion / interpreter shutdown) left the
    entry orphaned: no daemon thread existed to call the ``finally``
    block that removes it, so the entry (holding ``self``, the
    snapshot, and the dictated text) leaked for the process lifetime.
    """
    from voice_typer.server import clipboard as clip_mod
    from voice_typer.server.clipboard import ClipboardManager
    from voice_typer.server.clipboard_snapshot import ClipboardSnapshot
    import voice_typer.server.clipboard.manager as mgr_mod

    cm = _make_cm()
    snap = ClipboardSnapshot(
        platform="linux-x11",
        items=[("text/plain", b"prior clipboard content")],
        captured_at=0.0,
    )

    # FakeThread: simulates a thread that cannot be started.
    class FakeThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            raise RuntimeError("simulated out of thread resources")

    plumbing = (
        patch.object(clip_mod, "is_windows", return_value=False),
        patch.object(clip_mod, "is_macos", return_value=False),
        patch.object(clip_mod, "_Controller", MagicMock()),
        patch.object(clip_mod, "_Key", MagicMock()),
        patch.object(ClipboardManager, "_is_safe_paste_target", return_value=True),
        patch.object(ClipboardManager, "_detect_focused_process", return_value=None),
        patch.object(ClipboardManager, "_release_stuck_modifiers", lambda self: None),
        patch.object(ClipboardManager, "_safe_key_press", lambda self, *a, **kw: None),
        patch.object(clip_mod, "log"),
    )
    exits = []
    for p in plumbing:
        p.__enter__()
        exits.append(p)
    try:
        # Patch threading.Thread on the manager module's threading ref
        # so the ``threading.Thread(...).start()`` call in paste() uses
        # our FakeThread.
        with patch.object(mgr_mod.threading, "Thread", FakeThread):
            # paste() should NOT raise even though start() failed.
            result = cm.paste(snapshot=snap, pasted_text="the dictation")
            # paste() returns False here because the rate-limit / safety
            # guards short-circuit after the thread-start section; the
            # return value is irrelevant to this test — we only care
            # that _pending_restores is empty.
            assert result in (True, False)
    finally:
        for p in reversed(exits):
            p.__exit__(None, None, None)

    # ER-72 core assertion: the orphaned entry must have been removed.
    with clip_mod._pending_restores_lock:
        assert clip_mod._pending_restores == [], (
            f"_pending_restores should be empty after failed thread start (no leak); "
            f"got {clip_mod._pending_restores!r}"
        )


def test_pending_restores_no_leak_warning_logged() -> None:
    """ER-72: a failed ``Thread().start()`` logs a WARNING so the
    operator can diagnose the resource starvation."""
    from voice_typer.server import clipboard as clip_mod
    from voice_typer.server.clipboard import ClipboardManager
    from voice_typer.server.clipboard_snapshot import ClipboardSnapshot
    import voice_typer.server.clipboard.manager as mgr_mod

    cm = _make_cm()
    snap = ClipboardSnapshot(
        platform="linux-x11",
        items=[("text/plain", b"prior")],
        captured_at=0.0,
    )

    class FakeThread:
        def __init__(self, *a, **kw):
            pass

        def start(self):
            raise OSError("too many open files")

    plumbing = (
        patch.object(clip_mod, "is_windows", return_value=False),
        patch.object(clip_mod, "is_macos", return_value=False),
        patch.object(clip_mod, "_Controller", MagicMock()),
        patch.object(clip_mod, "_Key", MagicMock()),
        patch.object(ClipboardManager, "_is_safe_paste_target", return_value=True),
        patch.object(ClipboardManager, "_detect_focused_process", return_value=None),
        patch.object(ClipboardManager, "_release_stuck_modifiers", lambda self: None),
        patch.object(ClipboardManager, "_safe_key_press", lambda self, *a, **kw: None),
    )
    exits = []
    for p in plumbing:
        p.__enter__()
        exits.append(p)
    try:
        mock_log = MagicMock()
        with patch.object(clip_mod, "log", mock_log), patch.object(
            mgr_mod.threading, "Thread", FakeThread
        ):
            cm.paste(snapshot=snap, pasted_text="text")
    finally:
        for p in reversed(exits):
            p.__exit__(None, None, None)

    # A WARNING-level call should have been made.
    warning_calls = [c for c in mock_log.warning.call_args_list]
    assert len(warning_calls) >= 1, (
        f"expected at least one log.warning call on failed thread start; "
        f"got warning={warning_calls}"
    )


# ===========================================================================
# ER-79 — credential_store._read_plaintext_fallback mtime cache
# ===========================================================================


def test_read_plaintext_fallback_uses_mtime_cache(monkeypatch, tmp_path) -> None:
    """ER-79: repeated calls with the same ``st_mtime_ns`` hit the cache
    — the underlying file read happens once, not once per provider."""
    import os

    from voice_typer.server import config as _config_mod
    from voice_typer.server import credential_store

    # Set up a real config.json in tmp_path with two provider keys.
    config_data = {"openai_api_key": "sk-openai", "groq_api_key": "sk-groq"}
    config_file = tmp_path / "config.json"
    config_file.write_text(_json.dumps(config_data), encoding="utf-8")

    # Point _config_dir at tmp_path and clear the module-level cache.
    monkeypatch.setattr(_config_mod, "_config_dir", lambda: tmp_path)
    monkeypatch.setattr(credential_store, "_plaintext_config_cache", {})

    # Spy on _secure_read_text to count actual file reads.
    real_read = _config_mod._secure_read_text
    call_count = {"n": 0}

    def counting_read(path, **kw):
        call_count["n"] += 1
        return real_read(path, **kw)

    monkeypatch.setattr(_config_mod, "_secure_read_text", counting_read)

    # Mock os.stat to return a controlled, fixed mtime_ns. ``Path.exists``
    # also calls os.stat; the MagicMock return value is truthy so
    # exists() returns True.
    fake_stat = MagicMock()
    fake_stat.st_mtime_ns = 999
    monkeypatch.setattr(os, "stat", lambda *a, **kw: fake_stat)

    # First call — cache miss, reads file.
    assert credential_store._read_plaintext_fallback("openai") == "sk-openai"
    assert call_count["n"] == 1, f"first call should read file once, got {call_count['n']}"

    # Second call — same mtime → cache hit, NO read.
    assert credential_store._read_plaintext_fallback("groq") == "sk-groq"
    assert call_count["n"] == 1, (
        f"second call should hit cache (0 new reads), got {call_count['n']}"
    )

    # Change mtime → cache invalidated, next call reads again.
    fake_stat.st_mtime_ns = 1000
    assert credential_store._read_plaintext_fallback("openai") == "sk-openai"
    assert call_count["n"] == 2, (
        f"third call should re-read after mtime change, got {call_count['n']}"
    )


def test_read_plaintext_fallback_cache_is_per_path(monkeypatch, tmp_path) -> None:
    """ER-79: the cache is keyed by absolute file path — two different
    config dirs use separate cache entries."""
    import os

    from voice_typer.server import config as _config_mod
    from voice_typer.server import credential_store

    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()
    (dir_a / "config.json").write_text(
        _json.dumps({"openai_api_key": "sk-a"}), encoding="utf-8"
    )
    (dir_b / "config.json").write_text(
        _json.dumps({"openai_api_key": "sk-b"}), encoding="utf-8"
    )

    monkeypatch.setattr(credential_store, "_plaintext_config_cache", {})

    real_read = _config_mod._secure_read_text
    call_count = {"n": 0}

    def counting_read(path, **kw):
        call_count["n"] += 1
        return real_read(path, **kw)

    monkeypatch.setattr(_config_mod, "_secure_read_text", counting_read)

    fake_stat = MagicMock()
    fake_stat.st_mtime_ns = 1
    monkeypatch.setattr(os, "stat", lambda *a, **kw: fake_stat)

    # Switch _config_dir between the two dirs.
    current_dir = {"v": dir_a}
    monkeypatch.setattr(_config_mod, "_config_dir", lambda: current_dir["v"])

    assert credential_store._read_plaintext_fallback("openai") == "sk-a"
    assert call_count["n"] == 1

    # Switch to dir_b — different path, cache miss even though mtime is same.
    current_dir["v"] = dir_b
    assert credential_store._read_plaintext_fallback("openai") == "sk-b"
    assert call_count["n"] == 2, (
        f"different config dir should be a cache miss, got {call_count['n']} reads"
    )

    # Back to dir_a — same path + same mtime → cache hit.
    current_dir["v"] = dir_a
    assert credential_store._read_plaintext_fallback("openai") == "sk-a"
    assert call_count["n"] == 2, "returning to dir_a should hit the cache"
