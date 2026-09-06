"""#13: tests for the extracted tray_menu module.

Verifies that:
- display_hotkey formats pynput hotkey strings correctly
- wrap_callback suppresses SystemExit (ERR-QUIT-002)
- build_menu_for_tray (the shipped renderer) produces the expected menu structure
"""

from unittest.mock import MagicMock

import pytest


def _make_fake_tray(hotkey="<f2>", state="idle", left_click="open_app"):
    """A minimal fake TrayIcon satisfying build_menu_for_tray's reads.

    Covers only the attributes the shipped renderer consults; tests
    driving it assert on the pystray MenuItem calls the renderer makes.
    """
    from voice_typer.server.tray_types import AppState

    states = {
        "idle": AppState.IDLE,
        "recording": AppState.RECORDING,
        "transcribing": AppState.TRANSCRIBING,
    }
    tray = MagicMock()
    tray._hotkey = hotkey
    tray._config = MagicMock(hotkey=hotkey, tray_left_click_action=left_click)
    tray._state = states[state]
    tray._cached_menu = None
    tray._menu_cache_valid = False
    tray._menu_lock = MagicMock()
    tray._menu_lock.__enter__ = MagicMock(return_value=None)
    tray._menu_lock.__exit__ = MagicMock(return_value=False)
    tray._build_models_submenu = MagicMock(return_value=[])
    tray._build_microphones_submenu = MagicMock(return_value=[])
    tray._open_page = MagicMock()
    tray.open_electron_window = MagicMock()
    tray._confirm_quit_while_recording = MagicMock()
    return tray


def _install_fake_pystray(monkeypatch):
    """Replace tray_menu.pystray with a recording fake.

    Returns ``(items_created, restore)`` where ``items_created`` collects
    every MenuItem the renderer builds (label/default captured).
    """
    import voice_typer.server.tray_menu as tray_menu_mod

    items_created = []

    def fake_menu_item(label, callback=None, **kw):
        item = MagicMock()
        item.label = label
        item.callback = callback
        item.default = kw.get("default", False)
        items_created.append(item)
        return item

    fake = MagicMock()
    fake.SEPARATOR = "SEP"
    fake.MenuItem = fake_menu_item
    fake.Menu = MagicMock(return_value=MagicMock())
    monkeypatch.setattr(tray_menu_mod, "pystray", fake)
    # Identity-localize so assertions can pin the exact i18n keys the
    # shipped renderer emits (the real `_` translates keys to labels).
    monkeypatch.setattr(tray_menu_mod, "_", lambda k: k)
    return items_created


class TestDisplayHotkey:
    """display_hotkey formats pynput hotkey strings."""

    def test_f2_default(self):
        from voice_typer.server.tray_menu import display_hotkey

        assert display_hotkey("<f2>") == "F2"

    def test_custom_hotkey(self):
        from voice_typer.server.tray_menu import display_hotkey

        assert display_hotkey("<ctrl>+<shift>+d") == "Ctrl+Shift+D"

    def test_falls_back_to_default_when_empty(self):
        from voice_typer.server.tray_menu import display_hotkey

        assert display_hotkey("", fallback="<f4>") == "F4"

    def test_falls_back_to_default_when_none(self):
        from voice_typer.server.tray_menu import display_hotkey

        assert display_hotkey(None, fallback="<f9>") == "F9"


class TestWrapCallback:
    """wrap_callback wraps no-arg callbacks for pystray's (icon, item) signature."""

    def test_normal_callback_invoked(self):
        from voice_typer.server.tray_menu import wrap_callback

        called = []

        def cb():
            called.append("yes")

        wrapped = wrap_callback(cb)
        wrapped("icon", "item")  # pystray passes (icon, item)
        assert called == ["yes"]

    def test_system_exit_suppressed(self):
        """ERR-QUIT-002: SystemExit must be suppressed (not re-raised)
        so pystray doesn't print a traceback."""
        from voice_typer.server.tray_menu import wrap_callback

        def cb():
            raise SystemExit(0)

        wrapped = wrap_callback(cb)
        # Should NOT raise — SystemExit is caught and suppressed.
        wrapped("icon", "item")

    def test_exceptions_other_than_system_exit_propagate(self):
        from voice_typer.server.tray_menu import wrap_callback

        def cb():
            raise RuntimeError("boom")

        wrapped = wrap_callback(cb)
        with pytest.raises(RuntimeError, match="boom"):
            wrapped("icon", "item")


class TestBuildMenuForTray:
    """build_menu_for_tray (the shipped renderer) returns the expected menu structure."""

    def test_menu_has_toggle_open_models_restart_quit(self, monkeypatch):
        items_created = _install_fake_pystray(monkeypatch)
        from voice_typer.server.tray_menu import build_menu_for_tray

        result = build_menu_for_tray(_make_fake_tray())
        labels = [it.label for it in items_created]
        # labels now use localization keys by default
        assert any("toggle_dictation" in lbl for lbl in labels)
        assert "open_app" in labels
        assert "models" in labels
        assert "microphones" in labels
        assert "restart" in labels
        assert "quit" in labels
        # The renderer returns the cached tuple of items.
        assert result is not None

    def test_menu_uses_display_hotkey_for_toggle_label(self, monkeypatch):
        """The 'Start Dictation' label must include the formatted hotkey."""
        items_created = _install_fake_pystray(monkeypatch)
        from voice_typer.server.tray_menu import build_menu_for_tray

        build_menu_for_tray(_make_fake_tray(hotkey="<f5>"))
        toggle_label = next(it.label for it in items_created if "toggle_dictation" in it.label)
        assert "F5" in toggle_label, f"Start Dictation label should include formatted hotkey 'F5', got: {toggle_label}"

    def test_toggle_dictation_is_default_action_when_configured(self, monkeypatch):
        """The default menu item follows the configured left-click action."""
        items_created = _install_fake_pystray(monkeypatch)
        from voice_typer.server.tray_menu import build_menu_for_tray

        build_menu_for_tray(_make_fake_tray(hotkey="<f2>", left_click="toggle_dictation"))
        default_items = [it for it in items_created if it.default]
        assert len(default_items) == 1
        # With left_click == toggle_dictation the dictation item is bold.
        assert "toggle_dictation" in default_items[0].label

    def test_open_app_is_default_action_when_configured(self, monkeypatch):
        """With the default left-click action, Open App is the bold item."""
        items_created = _install_fake_pystray(monkeypatch)
        from voice_typer.server.tray_menu import build_menu_for_tray

        build_menu_for_tray(_make_fake_tray(left_click="open_app"))
        default_items = [it for it in items_created if it.default]
        assert len(default_items) == 1
        assert "open_app" in default_items[0].label

    def test_menu_result_is_cached(self, monkeypatch):
        """A second render with a valid cache returns the cached tuple."""
        _install_fake_pystray(monkeypatch)
        from voice_typer.server.tray_menu import build_menu_for_tray

        tray = _make_fake_tray()
        first = build_menu_for_tray(tray)
        tray._menu_cache_valid = True
        second = build_menu_for_tray(tray)
        assert second is first


# =============================================================================
# === Merged from test_new_perf_consolidated.py (: tray models cache) ===
# =============================================================================
"""Regression tests for NEW-PERF-004: tray models submenu caching.

Previously, every tray right-click triggered:
- ``ensure_hf_env()`` (filesystem checks)
- ``import qwen_asr`` (50–150 ms heavy ML import)
- 5+ filesystem ``exists()`` calls (one per candidate model)

This caused noticeable menu-open lag.  The fix caches:
- The qwen_asr import availability (session-lifetime).
- The HuggingFace hub snapshot-completeness probe (5-second TTL).

The cache is invalidated explicitly when a model download completes
(via ``invalidate_model_availability_cache()``).
"""

import time as _time  # noqa: E402
from contextlib import nullcontext  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest.mock import patch  # noqa: E402

from voice_typer.server import tray_models  # noqa: E402
from voice_typer.server.tray_models import (  # noqa: E402
    _HF_DOWNLOAD_CACHE_TTL_SECONDS,
    _check_hf_model_downloaded,
    invalidate_model_availability_cache,
)


def _hf_download_cache_lock_for_test():
    """Return a no-op context manager — the cache dict is not locked
    at the module level (it's only accessed from the tray thread).
    For test purposes we treat it as unlocked."""
    return nullcontext()


class TestHfDownloadCache:
    """The HuggingFace download check must be TTL-cached."""

    @pytest.fixture(autouse=True)
    def _clean_cache(self):
        """Reset the model-availability cache before/after each test."""
        invalidate_model_availability_cache()
        yield
        invalidate_model_availability_cache()

    def test_exists_called_once_within_ttl(self, tmp_path):
        """Within the TTL window, the filesystem ``is_dir()`` check
        must run at most once — subsequent calls hit the cache.
        """
        repo_id = "test/repo"
        config_dir = tmp_path

        # Patch Path.is_dir to count calls (the availability probe's
        # first gate is the repo-dir existence check).
        original_is_dir = Path.is_dir
        call_count = [0]

        def counting_is_dir(self):
            call_count[0] += 1
            return original_is_dir(self)

        with patch.object(Path, "is_dir", counting_is_dir):
            result1 = _check_hf_model_downloaded(repo_id, config_dir)
            result2 = _check_hf_model_downloaded(repo_id, config_dir)
            result3 = _check_hf_model_downloaded(repo_id, config_dir)

        # Only the first call should have hit the filesystem.
        assert call_count[0] == 1, (
            f"is_dir() called {call_count[0]} times; expected 1 (TTL cache should serve subsequent calls)"
        )
        # All three results must agree.
        assert result1 == result2 == result3

    def test_cache_expires_after_ttl(self, tmp_path):
        """After the TTL window, the next call must re-check the filesystem."""
        repo_id = "test/repo"
        config_dir = tmp_path

        # First call populates the cache.
        result1 = _check_hf_model_downloaded(repo_id, config_dir)

        # Manually backdate the cache entry so it's past the TTL.
        with _hf_download_cache_lock_for_test():
            key = (repo_id, str(config_dir))
            if key in tray_models._hf_download_cache:
                downloaded, _ = tray_models._hf_download_cache[key]
                tray_models._hf_download_cache[key] = (
                    downloaded,
                    _time.monotonic() - _HF_DOWNLOAD_CACHE_TTL_SECONDS - 1,
                )

        # Patch is_dir to verify it's called again.
        original_is_dir = Path.is_dir
        call_count = [0]

        def counting_is_dir(self):
            call_count[0] += 1
            return original_is_dir(self)

        with patch.object(Path, "is_dir", counting_is_dir):
            result2 = _check_hf_model_downloaded(repo_id, config_dir)

        assert call_count[0] == 1, "is_dir() should be called once after TTL expired"
        assert result2 == result1

    def test_different_repos_cached_separately(self, tmp_path):
        """Each repo_id gets its own cache entry."""
        config_dir = tmp_path
        _check_hf_model_downloaded("org/repo1", config_dir)
        _check_hf_model_downloaded("org/repo2", config_dir)

        # Both should be False (neither exists in tmp_path) but cached
        # separately.
        cache = tray_models._hf_download_cache
        assert ("org/repo1", str(config_dir)) in cache
        assert ("org/repo2", str(config_dir)) in cache

    def test_different_config_dirs_cached_separately(self, tmp_path):
        """Each config_dir gets its own cache namespace."""
        dir1 = tmp_path / "dir1"
        dir1.mkdir()
        dir2 = tmp_path / "dir2"
        dir2.mkdir()

        _check_hf_model_downloaded("org/repo", dir1)
        _check_hf_model_downloaded("org/repo", dir2)

        cache = tray_models._hf_download_cache
        assert ("org/repo", str(dir1)) in cache
        assert ("org/repo", str(dir2)) in cache


class TestInvalidateCache:
    """``invalidate_model_availability_cache`` must clear both caches."""

    @pytest.fixture(autouse=True)
    def _clean_cache(self):
        """Reset the model-availability cache before/after each test."""
        invalidate_model_availability_cache()
        yield
        invalidate_model_availability_cache()

    def test_invalidate_clears_hf_cache(self, tmp_path):
        _check_hf_model_downloaded("org/repo", tmp_path)
        assert len(tray_models._hf_download_cache) > 0

        invalidate_model_availability_cache()

        assert len(tray_models._hf_download_cache) == 0


class TestBuildModelsSubmenuUsesCache:
    """The full submenu builder must use the cached helpers."""

    @pytest.fixture(autouse=True)
    def _clean_cache(self):
        """Reset the model-availability cache before/after each test."""
        invalidate_model_availability_cache()
        yield
        invalidate_model_availability_cache()

    def test_two_consecutive_builds_return_five_candidates(self, tmp_path):
        """Two consecutive ``build_models_submenu_data`` calls must both
        return the 5 candidates (tiny / large-v3 / large-v3-turbo /
        parakeet / qwen — the catalog; ``large-v3`` was restored
        2026-08-15 at the user's request). No qwen_asr pip gate exists
        anymore — Qwen is a built-in ONNX backend (2026-08-15)."""
        # Provide a Config-like object so we skip the disk read.
        config_provider = MagicMock()
        config_provider.model_size = "tiny"
        config_provider.asr_backend = "whisper"

        # Mock ensure_hf_env to no-op.
        with patch("voice_typer.server.asr_setup.ensure_hf_env", lambda: None):
            data1 = tray_models.build_models_submenu_data(
                lambda: tmp_path,
                lambda name: None,
                config_provider=config_provider,
            )
            data2 = tray_models.build_models_submenu_data(
                lambda: tmp_path,
                lambda name: None,
                config_provider=config_provider,
            )

        # Both calls must succeed and return 5 candidates.
        assert len(data1) == 5
        assert len(data2) == 5


class TestQwenTrayAvailabilityAlignsWithModelsPage:
    """Qwen's ``downloaded`` flag in the tray submenu must mirror the
    Models page's ``get_model_status`` semantics.

    The Models page defines ``downloaded`` as model WEIGHTS on disk
    (``qwen_model_path`` directory OR the HF cache holding the ONNX
    export repo). Qwen is a built-in ONNX backend now (2026-08-15 —
    qwen_onnx_model.py, no ``qwen_asr`` pip package), so there is no
    separate ``deps_ok`` package gate: ``downloaded`` alone drives
    selectability.
    """

    @pytest.fixture(autouse=True)
    def _clean_cache(self):
        """Reset the model-availability cache before/after each test."""
        invalidate_model_availability_cache()
        yield
        invalidate_model_availability_cache()

    def _make_config(self, qwen_model_path=None):
        config = MagicMock()
        config.model_size = "tiny"
        config.asr_backend = "whisper"
        config.qwen_model_path = qwen_model_path
        return config

    def _qwen_downloaded_flag(self, tmp_path, config):
        """Return the ``downloaded`` flag for the qwen candidate row."""
        with patch("voice_typer.server.asr_setup.ensure_hf_env", lambda: None):
            data = tray_models.build_models_submenu_data(
                lambda: tmp_path,
                lambda name: None,
                config_provider=config,
            )
        qwen_row = next(row for row in data if row[0] == "qwen")
        return qwen_row[1]

    def test_hidden_when_nothing_on_disk(self, tmp_path):
        """No ``qwen_model_path`` dir and no HF cache → Qwen must NOT be
        listed as downloaded (weights are the only gate now)."""
        downloaded = self._qwen_downloaded_flag(tmp_path, self._make_config(qwen_model_path=None))
        assert downloaded is False

    def test_visible_when_model_path_points_at_existing_dir(self, tmp_path):
        """``qwen_model_path`` pointing at an existing directory → Qwen
        listed (matches ``_compute_model_status``)."""
        model_dir = tmp_path / "qwen-weights"
        model_dir.mkdir()
        downloaded = self._qwen_downloaded_flag(tmp_path, self._make_config(qwen_model_path=str(model_dir)))
        assert downloaded is True

    def test_visible_when_hf_cache_holds_repo(self, tmp_path, monkeypatch):
        """HF cache holding the Qwen ONNX repo dir → Qwen listed
        (matches ``_compute_model_status``'s ``qwen_in_cache``). The
        availability check delegates completeness to
        ``is_model_snapshot_complete`` — stub it True here (the
        cache-layout mechanics are pinned in
        tests/model_download/test_download_abort_gate.py)."""
        repo_dir = tmp_path / "huggingface" / "hub" / "models--andrewleech--qwen3-asr-1.7b-onnx"
        repo_dir.mkdir(parents=True)
        monkeypatch.setattr(
            "voice_typer.server.transcription_download.is_model_snapshot_complete",
            lambda repo_id: True,
        )
        downloaded = self._qwen_downloaded_flag(tmp_path, self._make_config(qwen_model_path=None))
        assert downloaded is True

    def test_config_json_path_used_when_no_provider(self, tmp_path):
        """config_provider=None reads ``qwen_model_path`` from config.json
        on disk (the live-config fallback path)."""
        import json

        model_dir = tmp_path / "qwen-weights"
        model_dir.mkdir()
        (tmp_path / "config.json").write_text(
            json.dumps({"qwen_model_path": str(model_dir)}),
            encoding="utf-8",
        )
        with patch("voice_typer.server.asr_setup.ensure_hf_env", lambda: None):
            data = tray_models.build_models_submenu_data(
                lambda: tmp_path,
                lambda name: None,
                config_provider=None,
            )
        qwen_row = next(row for row in data if row[0] == "qwen")
        assert qwen_row[1] is True, "qwen_model_path from config.json must count as downloaded for the tray"
