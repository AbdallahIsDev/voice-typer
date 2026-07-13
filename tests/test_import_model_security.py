"""RW-5: regression tests for import_model path validation + symlink rejection.

Verifies that:

1. The ``_handle_import_model`` IPC handler rejects ``dir_path`` values
   that fall outside the allowed roots (home directory, OS temp dir, or
   the HF cache).  Without this check, an IPC payload could request
   scanning — and copying into the app's HF cache — any directory on
   the filesystem.

2. ``VoiceTyperService.import_model`` refuses to copy a model cache
   that contains a symlink.  ``shutil.copytree`` with the legacy
   ``symlinks=True`` flag preserved symlinks verbatim, so a poisoned
   model dir with a symlink to ``~/.ssh/id_rsa`` would be copied into
   the cache.  Even with ``symlinks=False`` (the new default), copytree
   *follows* symlinks and copies the target's contents — so the
   explicit pre-check is the primary gate.

3. A legitimate model dir (no symlinks) still imports successfully —
   the false-positive guard.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ── Helpers (mirror tests/test_model_import.py) ──────────────────────


def _hf_cache_dir_name(repo_id: str) -> str:
    """Convert a HuggingFace repo ID to the cache directory name."""
    return f"models--{repo_id.replace('/', '--')}"


def _make_model_cache_dir(parent: Path, repo_id: str) -> Path:
    """Create a minimal HF cache subdirectory structure under ``parent``."""
    dir_name = _hf_cache_dir_name(repo_id)
    model_dir = parent / dir_name
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "blobs").mkdir(exist_ok=True)
    (model_dir / "refs").mkdir(exist_ok=True)
    (model_dir / "snapshots").mkdir(exist_ok=True)
    (model_dir / ".no_exist").write_text("placeholder")
    return model_dir


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def service():
    """Build a VoiceTyperService with a mock app for import_model tests."""
    from voice_typer.server.service import VoiceTyperService
    return VoiceTyperService(MagicMock())


# ── Tests: path validation ────────────────────────────────────────────


class TestImportPathValidation:
    """RW-5: ``_validate_import_path`` rejects paths outside allowed roots."""

    def test_rejects_path_outside_allowed_roots(self, tmp_path, monkeypatch):
        """A path outside home, temp, and the HF cache is rejected.

        We monkeypatch all three allowed roots to subdirs of ``tmp_path``
        so we can construct a sibling directory that is guaranteed to be
        outside all of them, regardless of where the test runner's
        actual home/temp live.
        """
        from voice_typer.server import config as cfg

        fake_home = tmp_path / "home"
        fake_home.mkdir()
        fake_temp = tmp_path / "temp"
        fake_temp.mkdir()
        fake_cache_root = tmp_path / "cache_root"
        fake_cache_root.mkdir()

        # Patch all allowed roots to subdirs of tmp_path.
        monkeypatch.setattr(cfg.Path, "home", lambda: fake_home)
        # ``_validate_import_path`` does ``import tempfile`` locally, so
        # we patch the global ``tempfile.gettempdir``.
        import tempfile as _tempfile_mod
        monkeypatch.setattr(_tempfile_mod, "gettempdir", lambda: str(fake_temp))
        monkeypatch.setattr(cfg, "_config_dir", lambda: fake_cache_root)
        monkeypatch.delenv("HF_HOME", raising=False)

        bad = tmp_path / "outside"  # sibling of all fake roots — not within any
        bad.mkdir()

        with pytest.raises(ValueError, match="outside the allowed roots"):
            cfg._validate_import_path(str(bad))

    def test_accepts_path_under_home(self, tmp_path, monkeypatch):
        """A path under the user's home directory is accepted."""
        from voice_typer.server import config as cfg

        fake_home = tmp_path / "home"
        fake_home.mkdir()
        fake_temp = tmp_path / "temp"
        fake_temp.mkdir()
        fake_cache_root = tmp_path / "cache_root"
        fake_cache_root.mkdir()

        monkeypatch.setattr(cfg.Path, "home", lambda: fake_home)
        import tempfile as _tempfile_mod
        monkeypatch.setattr(_tempfile_mod, "gettempdir", lambda: str(fake_temp))
        monkeypatch.setattr(cfg, "_config_dir", lambda: fake_cache_root)
        monkeypatch.delenv("HF_HOME", raising=False)

        downloads = fake_home / "Downloads" / "models"
        downloads.mkdir(parents=True)

        result = cfg._validate_import_path(str(downloads))
        assert result == str(downloads.resolve())

    def test_accepts_path_under_temp(self, tmp_path, monkeypatch):
        """A path under the OS temp directory is accepted."""
        from voice_typer.server import config as cfg

        fake_home = tmp_path / "home"
        fake_home.mkdir()
        fake_temp = tmp_path / "temp"
        fake_temp.mkdir()
        fake_cache_root = tmp_path / "cache_root"
        fake_cache_root.mkdir()

        monkeypatch.setattr(cfg.Path, "home", lambda: fake_home)
        import tempfile as _tempfile_mod
        monkeypatch.setattr(_tempfile_mod, "gettempdir", lambda: str(fake_temp))
        monkeypatch.setattr(cfg, "_config_dir", lambda: fake_cache_root)
        monkeypatch.delenv("HF_HOME", raising=False)

        sub = fake_temp / "extracted_model"
        sub.mkdir()

        result = cfg._validate_import_path(str(sub))
        assert result == str(sub.resolve())

    def test_accepts_path_under_app_hf_cache(self, tmp_path, monkeypatch):
        """A path under the app's own HF cache is accepted (re-import)."""
        from voice_typer.server import config as cfg

        fake_home = tmp_path / "home"
        fake_home.mkdir()
        fake_temp = tmp_path / "temp"
        fake_temp.mkdir()
        fake_cache_root = tmp_path / "cache_root"
        fake_cache_root.mkdir()

        monkeypatch.setattr(cfg.Path, "home", lambda: fake_home)
        import tempfile as _tempfile_mod
        monkeypatch.setattr(_tempfile_mod, "gettempdir", lambda: str(fake_temp))
        monkeypatch.setattr(cfg, "_config_dir", lambda: fake_cache_root)
        monkeypatch.delenv("HF_HOME", raising=False)

        cache = fake_cache_root / "huggingface" / "hub" / "models--x--y"
        cache.mkdir(parents=True)

        result = cfg._validate_import_path(str(cache))
        assert result == str(cache.resolve())

    def test_accepts_path_under_hf_home_env(self, tmp_path, monkeypatch):
        """A path under ``$HF_HOME`` is accepted when that env var is set."""
        from voice_typer.server import config as cfg

        fake_home = tmp_path / "home"
        fake_home.mkdir()
        fake_temp = tmp_path / "temp"
        fake_temp.mkdir()
        fake_cache_root = tmp_path / "cache_root"
        fake_cache_root.mkdir()
        fake_hf_home = tmp_path / "hf_home"
        fake_hf_home.mkdir()

        monkeypatch.setattr(cfg.Path, "home", lambda: fake_home)
        import tempfile as _tempfile_mod
        monkeypatch.setattr(_tempfile_mod, "gettempdir", lambda: str(fake_temp))
        monkeypatch.setattr(cfg, "_config_dir", lambda: fake_cache_root)
        monkeypatch.setenv("HF_HOME", str(fake_hf_home))

        sub = fake_hf_home / "hub" / "models--x--y"
        sub.mkdir(parents=True)

        result = cfg._validate_import_path(str(sub))
        assert result == str(sub.resolve())

    def test_resolves_dotdot_before_validation(self, tmp_path, monkeypatch):
        """``..`` sequences are resolved away before validation, so a
        path that *appears* to be under home (but actually escapes via
        ``..``) is rejected."""
        from voice_typer.server import config as cfg

        fake_home = tmp_path / "home"
        fake_home.mkdir()
        # Create a real subdir so the path resolves cleanly
        (fake_home / "Downloads").mkdir()
        fake_temp = tmp_path / "temp"
        fake_temp.mkdir()
        fake_cache_root = tmp_path / "cache_root"
        fake_cache_root.mkdir()

        monkeypatch.setattr(cfg.Path, "home", lambda: fake_home)
        import tempfile as _tempfile_mod
        monkeypatch.setattr(_tempfile_mod, "gettempdir", lambda: str(fake_temp))
        monkeypatch.setattr(cfg, "_config_dir", lambda: fake_cache_root)
        monkeypatch.delenv("HF_HOME", raising=False)

        # ``home/Downloads/../../outside`` resolves to ``tmp_path/outside``
        # which is NOT under any allowed root.
        bad = str(fake_home / "Downloads" / ".." / ".." / "outside")

        with pytest.raises(ValueError, match="outside the allowed roots"):
            cfg._validate_import_path(bad)


class TestImportPathValidationHandler:
    """RW-5: the IPC handler returns an error response for bad paths.

    These tests import ``ModelHandlersMixin`` via ``ipc_server`` (rather
    than directly via the ``handlers`` package) to avoid a pre-existing
    circular-import quirk: ``handlers/__init__.py`` eagerly imports
    ``config_handlers``, which imports helpers from ``ipc_server``.  If
    ``ipc_server`` has not been loaded yet, the circular import fails.
    Importing ``ipc_server`` first primes ``sys.modules`` so the
    handlers package can resolve its dependencies.
    """

    def test_handler_rejects_bad_path_with_error_response(
        self, tmp_path, monkeypatch
    ):
        """End-to-end: ``_handle_import_model`` returns an error response
        for paths outside the allowed roots, without calling
        ``service.import_model``."""
        # Prime the circular import.
        import voice_typer.server.ipc_server  # noqa: F401
        from voice_typer.server import config as cfg
        from voice_typer.server.handlers.model_handlers import ModelHandlersMixin

        # Point all allowed roots at tmp_path subdirs so we can construct
        # a guaranteed-outside path.
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        fake_temp = tmp_path / "temp"
        fake_temp.mkdir()
        fake_cache_root = tmp_path / "cache_root"
        fake_cache_root.mkdir()

        monkeypatch.setattr(cfg.Path, "home", lambda: fake_home)
        import tempfile as _tempfile_mod
        monkeypatch.setattr(_tempfile_mod, "gettempdir", lambda: str(fake_temp))
        monkeypatch.setattr(cfg, "_config_dir", lambda: fake_cache_root)
        monkeypatch.delenv("HF_HOME", raising=False)

        class Stub(ModelHandlersMixin):
            service = MagicMock()
            app = MagicMock()
            _send = MagicMock()

        stub = Stub()
        resp = {"type": "ack", "data": {}}
        bad_path = str(tmp_path / "outside")

        result = stub._handle_import_model({"dir_path": bad_path}, resp)

        assert result["type"] == "error", (
            f"Expected error response, got {result}"
        )
        assert "outside the allowed roots" in result["data"]["message"]
        # service.import_model must NOT have been called — the handler
        # short-circuits on validation failure.
        stub.service.import_model.assert_not_called()

    def test_handler_accepts_path_under_home(
        self, tmp_path, monkeypatch
    ):
        """End-to-end: ``_handle_import_model`` accepts a path under home
        and delegates to ``service.import_model``."""
        # Prime the circular import.
        import voice_typer.server.ipc_server  # noqa: F401
        from voice_typer.server import config as cfg
        from voice_typer.server.handlers.model_handlers import ModelHandlersMixin

        fake_home = tmp_path / "home"
        fake_home.mkdir()
        fake_temp = tmp_path / "temp"
        fake_temp.mkdir()
        fake_cache_root = tmp_path / "cache_root"
        fake_cache_root.mkdir()

        monkeypatch.setattr(cfg.Path, "home", lambda: fake_home)
        import tempfile as _tempfile_mod
        monkeypatch.setattr(_tempfile_mod, "gettempdir", lambda: str(fake_temp))
        monkeypatch.setattr(cfg, "_config_dir", lambda: fake_cache_root)
        monkeypatch.delenv("HF_HOME", raising=False)

        src = fake_home / "Downloads" / "my_models"
        src.mkdir(parents=True)

        class Stub(ModelHandlersMixin):
            service = MagicMock()
            app = MagicMock()
            _send = MagicMock()

        # Make service.import_model return a canned success.
        stub = Stub()
        stub.service.import_model.return_value = {
            "success": True,
            "imported": [],
            "found": [],
            "errors": [],
        }
        resp = {"type": "ack", "data": {}}

        result = stub._handle_import_model({"dir_path": str(src)}, resp)

        assert result["type"] == "import_model_result"
        stub.service.import_model.assert_called_once_with(str(src.resolve()))


# ── Tests: symlink rejection in import_model ──────────────────────────


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink tests")
class TestImportModelSymlinkRejection:
    """RW-5: ``import_model`` refuses to copy a model cache that contains
    symlinks."""

    def test_rejects_model_dir_with_symlinked_file(
        self, service, tmp_path, monkeypatch
    ):
        """A model cache dir containing a symlink to an external file
        must be REJECTED — not silently copied into the app cache."""
        monkeypatch.setattr(
            "voice_typer.server.config._config_dir", lambda: tmp_path / "app_hf"
        )
        monkeypatch.setattr(
            "voice_typer.server.tray_models.invalidate_model_availability_cache",
            lambda: None,
        )

        src_dir = tmp_path / "source"
        src_dir.mkdir()
        model_dir = _make_model_cache_dir(src_dir, "Systran/faster-whisper-tiny.en")

        # Plant a symlink inside the model cache pointing to a "secret"
        # file outside the source dir.
        secret = tmp_path / "secret_target"
        secret.write_text("super secret — should NOT be copied")
        link = model_dir / "leaked_secret"
        try:
            link.symlink_to(secret)
        except OSError:
            pytest.skip("Cannot create symlinks on this system")

        result = service.import_model(str(src_dir))

        # The call itself succeeds (success=True) but the poisoned model
        # is reported in errors and NOT in imported.
        assert result["success"] is True
        assert "tiny.en" not in result["imported"], (
            "Model containing a symlink must NOT be imported — "
            f"got imported={result['imported']}"
        )
        assert len(result["errors"]) == 1
        assert result["errors"][0]["model"] == "tiny.en"
        assert "symlink" in result["errors"][0]["error"].lower()

        # The destination must NOT exist in the app's cache.
        dest = tmp_path / "app_hf" / "huggingface" / "hub" / model_dir.name
        assert not dest.exists(), (
            "Destination dir must not be created when a symlink is detected"
        )

    def test_rejects_model_dir_with_symlinked_subdir(
        self, service, tmp_path, monkeypatch
    ):
        """A symlinked subdirectory inside the model cache must also be
        rejected (os.walk's default ``followlinks=False`` lists symlinked
        dirs in ``dirnames`` — the check must catch them)."""
        monkeypatch.setattr(
            "voice_typer.server.config._config_dir", lambda: tmp_path / "app_hf"
        )
        monkeypatch.setattr(
            "voice_typer.server.tray_models.invalidate_model_availability_cache",
            lambda: None,
        )

        src_dir = tmp_path / "source"
        src_dir.mkdir()
        model_dir = _make_model_cache_dir(src_dir, "Systran/faster-whisper-tiny.en")

        # Plant a symlinked subdirectory
        real_dir = tmp_path / "real_dir"
        real_dir.mkdir()
        (real_dir / "payload.txt").write_text("payload")
        link_dir = model_dir / "snapshots_link"
        try:
            link_dir.symlink_to(real_dir, target_is_directory=True)
        except OSError:
            pytest.skip("Cannot create symlinks on this system")

        result = service.import_model(str(src_dir))

        assert "tiny.en" not in result["imported"]
        assert len(result["errors"]) == 1
        assert result["errors"][0]["model"] == "tiny.en"
        assert "symlink" in result["errors"][0]["error"].lower()

    def test_rejects_symlink_to_etc_hostname(
        self, service, tmp_path, monkeypatch
    ):
        """The exact attack scenario from the RW-5 brief: a symlink to
        ``/etc/hostname`` (a canonical "sensitive file" target).  The
        import must be rejected, and the destination must not contain a
        copy of /etc/hostname's contents."""
        monkeypatch.setattr(
            "voice_typer.server.config._config_dir", lambda: tmp_path / "app_hf"
        )
        monkeypatch.setattr(
            "voice_typer.server.tray_models.invalidate_model_availability_cache",
            lambda: None,
        )

        src_dir = tmp_path / "source"
        src_dir.mkdir()
        model_dir = _make_model_cache_dir(src_dir, "Systran/faster-whisper-tiny.en")

        link = model_dir / "hostname_link"
        try:
            link.symlink_to("/etc/hostname")
        except OSError:
            pytest.skip("Cannot create symlinks on this system")

        # If /etc/hostname doesn't exist on this system, create a temp
        # file as the target instead — the point is to verify the
        # symlink is detected and rejected, not the specific target.
        if not link.exists():
            link.unlink()
            target = tmp_path / "hostname_fallback"
            target.write_text("fallback-hostname")
            link.symlink_to(target)

        result = service.import_model(str(src_dir))

        assert "tiny.en" not in result["imported"]
        assert len(result["errors"]) == 1
        assert "symlink" in result["errors"][0]["error"].lower()

        # Defense-in-depth: verify the destination was never created, so
        # even if the symlink pointed to a sensitive file, its contents
        # were not copied.
        dest = tmp_path / "app_hf" / "huggingface" / "hub" / model_dir.name
        assert not dest.exists()

    def test_legitimate_model_dir_imports_successfully(
        self, service, tmp_path, monkeypatch
    ):
        """False-positive guard: a model dir with NO symlinks must
        import normally.  This catches regressions where the symlink
        check is too aggressive."""
        monkeypatch.setattr(
            "voice_typer.server.config._config_dir", lambda: tmp_path / "app_hf"
        )
        monkeypatch.setattr(
            "voice_typer.server.tray_models.invalidate_model_availability_cache",
            lambda: None,
        )

        src_dir = tmp_path / "source"
        src_dir.mkdir()
        model_dir = _make_model_cache_dir(src_dir, "Systran/faster-whisper-tiny.en")
        # Add a regular (non-symlink) file to make the copy non-trivial.
        (model_dir / "config.json").write_text('{"model_type": "test"}')

        result = service.import_model(str(src_dir))

        assert result["success"] is True
        assert "tiny.en" in result["imported"]
        assert len(result["errors"]) == 0, (
            f"Expected no errors for legitimate import, got {result['errors']}"
        )

        # Verify the files were actually copied.
        dest = tmp_path / "app_hf" / "huggingface" / "hub" / model_dir.name
        assert dest.exists()
        assert (dest / ".no_exist").read_text() == "placeholder"
        assert (dest / "config.json").read_text() == '{"model_type": "test"}'

    def test_mixed_symlink_and_clean_models(
        self, service, tmp_path, monkeypatch
    ):
        """When the source dir contains both a poisoned model (with a
        symlink) and a clean model, the clean one must still import
        successfully while the poisoned one is rejected."""
        monkeypatch.setattr(
            "voice_typer.server.config._config_dir", lambda: tmp_path / "app_hf"
        )
        monkeypatch.setattr(
            "voice_typer.server.tray_models.invalidate_model_availability_cache",
            lambda: None,
        )

        src_dir = tmp_path / "source"
        src_dir.mkdir()

        # Clean model — should import
        clean_dir = _make_model_cache_dir(src_dir, "Systran/faster-whisper-tiny.en")
        (clean_dir / "config.json").write_text('{"model_type": "tiny"}')

        # Poisoned model — should be rejected
        poison_dir = _make_model_cache_dir(src_dir, "Systran/faster-whisper-small.en")
        secret = tmp_path / "secret"
        secret.write_text("secret")
        link = poison_dir / "leaked"
        try:
            link.symlink_to(secret)
        except OSError:
            pytest.skip("Cannot create symlinks on this system")

        result = service.import_model(str(src_dir))

        assert result["success"] is True
        assert "tiny.en" in result["imported"]
        assert "small.en" not in result["imported"]
        assert len(result["errors"]) == 1
        assert result["errors"][0]["model"] == "small.en"
        assert "symlink" in result["errors"][0]["error"].lower()

        # The clean model's destination should exist; the poisoned one's
        # should not.
        app_hf = tmp_path / "app_hf" / "huggingface" / "hub"
        assert (app_hf / clean_dir.name).exists()
        assert not (app_hf / poison_dir.name).exists()


# ── Tests: _is_path_within helper ─────────────────────────────────────


class TestIsPathWithin:
    """RW-5: the path-containment helper correctly respects directory
    boundaries (no prefix-collision false positives)."""

    def test_descendant_is_within(self, tmp_path):
        from voice_typer.server.config import _is_path_within
        parent = tmp_path
        child = tmp_path / "sub" / "dir"
        child.mkdir(parents=True)
        assert _is_path_within(child, parent) is True

    def test_same_path_is_within(self, tmp_path):
        from voice_typer.server.config import _is_path_within
        assert _is_path_within(tmp_path, tmp_path) is True

    def test_sibling_is_not_within(self, tmp_path):
        from voice_typer.server.config import _is_path_within
        a = tmp_path / "a"
        b = tmp_path / "b"
        a.mkdir()
        b.mkdir()
        assert _is_path_within(a, b) is False

    def test_prefix_collision_not_within(self, tmp_path):
        """``/home/userX`` must NOT be considered within ``/home/user``
        — a naive ``str.startswith`` would incorrectly accept it."""
        from voice_typer.server.config import _is_path_within
        user = tmp_path / "user"
        user.mkdir()
        user_x = tmp_path / "userX"
        user_x.mkdir()
        assert _is_path_within(user_x, user) is False

    def test_dotdot_resolved_before_comparison(self, tmp_path):
        """``parent/sub/../sub`` resolves to ``parent/sub`` and is
        therefore within ``parent``."""
        from voice_typer.server.config import _is_path_within
        sub = tmp_path / "sub"
        sub.mkdir()
        dotted = tmp_path / "sub" / ".." / "sub"
        assert _is_path_within(dotted, tmp_path) is True

    def test_parent_is_not_within_child(self, tmp_path):
        from voice_typer.server.config import _is_path_within
        child = tmp_path / "child"
        child.mkdir()
        assert _is_path_within(tmp_path, child) is False
