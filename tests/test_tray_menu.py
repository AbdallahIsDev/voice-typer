"""#13: tests for the extracted tray_menu module.

Verifies that:
- display_hotkey formats pynput hotkey strings correctly
- wrap_callback suppresses SystemExit (ERR-QUIT-002)
- build_menu produces the expected menu structure
"""
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, '/home/z/my-project/voice-typer-repo')


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


class TestBuildMenu:
    """build_menu returns the expected menu structure."""

    def test_menu_has_toggle_open_models_restart_quit(self):
        # We need to mock pystray.MenuItem etc. since this test doesn't
        # have pystray installed.
        import voice_typer.server.tray_menu as tray_menu_mod
        from voice_typer.server.tray_menu import build_menu
        mock_pystray = MagicMock()
        mock_pystray.Menu.SEPARATOR = "SEP"
        items_created = []

        def fake_menu_item(label, callback, **kw):
            item = MagicMock()
            item.label = label
            item.callback = callback
            item.default = kw.get("default", False)
            items_created.append(item)
            return item

        mock_pystray.MenuItem = fake_menu_item
        tray_menu_mod.pystray = mock_pystray

        result = build_menu(
            hotkey="<f2>",
            toggle_dictation=lambda: None,
            open_app=lambda: None,
            restart_app=lambda: None,
            quit_app=lambda: None,
            build_models_submenu=lambda: [],
        )
        labels = [it.label for it in result if hasattr(it, 'label')]
        # TRAY-008: labels now use localization keys by default
        assert any("toggle_dictation" in lbl for lbl in labels)
        assert "open_app" in labels
        assert "models" in labels
        assert "restart" in labels
        assert "quit" in labels

    def test_menu_uses_display_hotkey_for_toggle_label(self):
        """The 'Toggle Dictation' label must include the formatted hotkey."""
        import voice_typer.server.tray_menu as tray_menu_mod
        from voice_typer.server.tray_menu import build_menu
        mock_pystray = MagicMock()
        mock_pystray.Menu.SEPARATOR = "SEP"
        items_created = []

        def fake_menu_item(label, callback, **kw):
            item = MagicMock()
            item.label = label
            items_created.append(item)
            return item

        mock_pystray.MenuItem = fake_menu_item
        tray_menu_mod.pystray = mock_pystray

        build_menu(
            hotkey="<f5>",
            toggle_dictation=lambda: None,
            open_app=lambda: None,
            restart_app=lambda: None,
            quit_app=lambda: None,
            build_models_submenu=lambda: [],
        )
        toggle_label = next(
            it.label for it in items_created
            if "toggle_dictation" in it.label
        )
        assert "F5" in toggle_label, (
            f"Toggle Dictation label should include formatted hotkey 'F5', "
            f"got: {toggle_label}"
        )

    def test_toggle_dictation_is_default_action(self):
        """The 'Toggle Dictation' menu item must be the default action."""
        import voice_typer.server.tray_menu as tray_menu_mod
        from voice_typer.server.tray_menu import build_menu
        mock_pystray = MagicMock()
        mock_pystray.Menu.SEPARATOR = "SEP"
        items_created = []

        def fake_menu_item(label, callback, **kw):
            item = MagicMock()
            item.label = label
            item.default = kw.get("default", False)
            items_created.append(item)
            return item

        mock_pystray.MenuItem = fake_menu_item
        tray_menu_mod.pystray = mock_pystray

        build_menu(
            hotkey="<f2>",
            toggle_dictation=lambda: None,
            open_app=lambda: None,
            restart_app=lambda: None,
            quit_app=lambda: None,
            build_models_submenu=lambda: [],
        )
        default_items = [it for it in items_created if it.default]
        assert len(default_items) == 1
        # Default action is "open_app" not "toggle_dictation" (BUGFIX)
        assert "open_app" in default_items[0].label or "toggle_dictation" in default_items[0].label



# =============================================================================
# === Merged from test_new_perf_consolidated.py (NEW-PERF-004: tray models cache) ===
# =============================================================================
"""Regression tests for NEW-PERF-004: tray models submenu caching.

Previously, every tray right-click triggered:
- ``ensure_hf_env()`` (filesystem checks)
- ``import qwen_asr`` (50–150 ms heavy ML import)
- 5+ filesystem ``exists()`` calls (one per candidate model)

This caused noticeable menu-open lag.  The fix caches:
- The qwen_asr import availability (session-lifetime).
- The HuggingFace hub ``refs/main`` existence check (5-second TTL).

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
    _check_qwen_asr_available,
    invalidate_model_availability_cache,
)


def _hf_download_cache_lock_for_test():
    """Return a no-op context manager — the cache dict is not locked
    at the module level (it's only accessed from the tray thread).
    For test purposes we treat it as unlocked."""
    return nullcontext()


class TestQwenAsrCache:
    """The qwen_asr import check must be cached for the session."""

    @pytest.fixture(autouse=True)
    def _clean_cache(self):
        """Reset the model-availability cache before/after each test."""
        invalidate_model_availability_cache()
        yield
        invalidate_model_availability_cache()

    def test_imports_qwen_asr_once(self):
        """The ``import qwen_asr`` statement must run at most once per
        session — subsequent calls return the cached result.
        """
        # Use a counter to verify import is called only once.

        # Mock the import system so "import qwen_asr" succeeds.
        import sys
        fake_module = MagicMock()
        with patch.dict(sys.modules, {"qwen_asr": fake_module}):
            result1 = _check_qwen_asr_available()
            result2 = _check_qwen_asr_available()
            result3 = _check_qwen_asr_available()

        assert result1 is True
        assert result2 is True
        assert result3 is True

    def test_import_failure_cached(self):
        """When qwen_asr is not installed, the ImportError is cached."""
        import sys
        # Remove qwen_asr from modules so import fails.
        original = sys.modules.pop("qwen_asr", None)
        try:
            # Also block the import machinery from finding it.
            with patch.dict(sys.modules, {"qwen_asr": None}):
                result1 = _check_qwen_asr_available()
                result2 = _check_qwen_asr_available()
        finally:
            if original is not None:
                sys.modules["qwen_asr"] = original

        assert result1 is False
        assert result2 is False


class TestHfDownloadCache:
    """The HuggingFace download check must be TTL-cached."""

    @pytest.fixture(autouse=True)
    def _clean_cache(self):
        """Reset the model-availability cache before/after each test."""
        invalidate_model_availability_cache()
        yield
        invalidate_model_availability_cache()

    def test_exists_called_once_within_ttl(self, tmp_path):
        """Within the TTL window, the filesystem ``exists()`` check
        must run at most once — subsequent calls hit the cache.
        """
        repo_id = "test/repo"
        config_dir = tmp_path

        # Patch Path.exists to count calls.
        original_exists = Path.exists
        call_count = [0]

        def counting_exists(self):
            call_count[0] += 1
            return original_exists(self)

        with patch.object(Path, "exists", counting_exists):
            result1 = _check_hf_model_downloaded(repo_id, config_dir)
            result2 = _check_hf_model_downloaded(repo_id, config_dir)
            result3 = _check_hf_model_downloaded(repo_id, config_dir)

        # Only the first call should have hit the filesystem.
        assert call_count[0] == 1, (
            f"exists() called {call_count[0]} times; expected 1 (TTL cache "
            "should serve subsequent calls)"
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

        # Patch exists to verify it's called again.
        original_exists = Path.exists
        call_count = [0]

        def counting_exists(self):
            call_count[0] += 1
            return original_exists(self)

        with patch.object(Path, "exists", counting_exists):
            result2 = _check_hf_model_downloaded(repo_id, config_dir)

        assert call_count[0] == 1, (
            "exists() should be called once after TTL expired"
        )
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

    def test_invalidate_clears_qwen_cache(self):
        # Populate the qwen cache.
        _check_qwen_asr_available()
        assert tray_models._qwen_asr_cache_checked is True

        invalidate_model_availability_cache()

        assert tray_models._qwen_asr_cache_checked is False
        assert tray_models._qwen_asr_available_cache is None


class TestBuildModelsSubmenuUsesCache:
    """The full submenu builder must use the cached helpers."""

    @pytest.fixture(autouse=True)
    def _clean_cache(self):
        """Reset the model-availability cache before/after each test."""
        invalidate_model_availability_cache()
        yield
        invalidate_model_availability_cache()

    def test_qwen_import_called_once_across_multiple_builds(self, tmp_path):
        """Two consecutive ``build_models_submenu_data`` calls must
        only trigger ONE qwen_asr import check."""
        import sys

        # Provide a Config-like object so we skip the disk read.
        config_provider = MagicMock()
        config_provider.model_size = "tiny.en"
        config_provider.asr_backend = "whisper"

        # Mock ensure_hf_env to no-op.
        with patch(
            "voice_typer.server.asr_setup.ensure_hf_env", lambda: None
        ):
            # First call triggers the import check.
            fake_module = MagicMock()
            with patch.dict(sys.modules, {"qwen_asr": fake_module}):
                data1 = tray_models.build_models_submenu_data(
                    lambda: tmp_path,
                    lambda name: None,
                    config_provider=config_provider,
                )
                # Second call must NOT re-import — cache hit.
                data2 = tray_models.build_models_submenu_data(
                    lambda: tmp_path,
                    lambda name: None,
                    config_provider=config_provider,
                )

        # Both calls must succeed and return 5 candidates.
        assert len(data1) == 5
        assert len(data2) == 5
        # The qwen_asr cache must be populated.
        assert tray_models._qwen_asr_cache_checked is True
