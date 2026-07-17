"""Unit tests for ``voice_typer.server.logging_setup``.

The module exposes a single public function ``_setup_logging`` that:

1. Calls ``log.setup_logging(config_dir, ...)`` to install a
   :class:`logging.handlers.RotatingFileHandler` and an optional coloured
   :class:`_FlushingStreamHandler` on the ``voice_typer`` logger.
2. Sets ``HF_HOME`` under the config directory (so huggingface cache stays
   inside the user's voice-typer data dir rather than ``~/.cache``).
3. Validates environment variables (``_validate_env_vars``).
4. Warns if running inside a container (``warn_if_in_container``).
5. Installs the Windows VEH crash handler (no-op on POSIX).

These tests pin every observable side effect of the function.  They use
``tmp_path`` for the config dir, ``monkeypatch`` to stub out the non-logging
side effects, and an autouse fixture that snapshots & restores the
``voice_typer`` logger so tests don't pollute each other.
"""

from __future__ import annotations

import contextlib
import logging
import logging.handlers
import os
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from voice_typer.server import logging_setup
from voice_typer.server.log import _FlushingStreamHandler, close_devnull_files

# ─── Test isolation ────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _restore_logging_state():
    """Snapshot and restore logging state to prevent cross-test pollution.

    Saves handlers/filters/level on both the ``voice_typer`` logger and the
    true root logger, plus the module-level ``_session_id`` of
    :mod:`voice_typer.server.log`.  Anything the test installed is torn
    down at exit so the next test starts from a clean slate.
    """
    vt_root = logging.getLogger("voice_typer")
    saved_vt_handlers = list(vt_root.handlers)
    saved_vt_filters = list(vt_root.filters)
    saved_vt_level = vt_root.level
    true_root = logging.getLogger()
    saved_true_handlers = list(true_root.handlers)
    from voice_typer.server import log as _log_module

    saved_session_id = _log_module._session_id

    yield

    vt_root.handlers = saved_vt_handlers
    vt_root.filters = saved_vt_filters
    vt_root.setLevel(saved_vt_level)
    true_root.handlers = saved_true_handlers
    _log_module._session_id = saved_session_id
    # Close any devnull FDs opened if sys.stderr was None during the test
    # (defensive — pytest normally provides a real stderr).
    close_devnull_files()


# ─── Shared fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def config_dir(tmp_path: Path, monkeypatch) -> Path:
    """Point ``logging_setup._config_dir`` at a tmp_path-based directory.

    Patching the *local* reference inside ``logging_setup`` (rather than
    setting ``VOICE_TYPER_CONFIG_DIR``) avoids the SEC-005 path-traversal
    validation in ``config._config_dir`` that would reject a tmp_path
    outside ``Path.home()``.
    """
    d = tmp_path / "voice-typer-cfg"
    monkeypatch.setattr(logging_setup, "_config_dir", lambda: d)
    # _migrate_from_legacy is a no-op on a fresh tmp_path but stubbing it
    # guarantees no filesystem touch outside the config dir.
    monkeypatch.setattr(logging_setup, "_migrate_from_legacy", lambda: None)
    return d


@pytest.fixture
def clean_env(monkeypatch):
    """Clear VOICE_TYPER_* / HF_HOME env vars that affect _setup_logging."""
    for var in (
        "VOICE_TYPER_DEBUG",
        "VOICE_TYPER_QUIET",
        "VOICE_TYPER_LOG_JSON",
        "HF_HOME",
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def stub_side_effects(monkeypatch):
    """Replace non-logging side effects with MagicMock spies.

    Returns a dict of spies so individual tests can assert call counts /
    args without each test repeating the monkeypatch dance.
    """
    spies = {
        "validate_env": MagicMock(),
        "container_warn": MagicMock(),
        "crash_install": MagicMock(return_value=False),
        "crash_set_dir": MagicMock(),
    }
    monkeypatch.setattr(logging_setup, "_validate_env_vars", spies["validate_env"])
    # ``warn_if_in_container`` is imported *inside* _setup_logging, so the
    # patch target is the source module, not logging_setup.
    monkeypatch.setattr(
        "voice_typer.server.container_detect.warn_if_in_container",
        spies["container_warn"],
    )
    monkeypatch.setattr(
        "voice_typer.server.crash_handler.install_crash_handler",
        spies["crash_install"],
    )
    monkeypatch.setattr(
        "voice_typer.server.crash_handler.set_crash_handler_config_dir",
        spies["crash_set_dir"],
    )
    return spies


def _vt_handlers() -> list[logging.Handler]:
    return logging.getLogger("voice_typer").handlers


def _flush_all() -> None:
    for h in _vt_handlers():
        with contextlib.suppress(Exception):
            h.flush()


# ─── Handler installation ─────────────────────────────────────────────────


def test_installs_rotating_file_handler(config_dir, clean_env, stub_side_effects):
    """_setup_logging installs exactly one RotatingFileHandler."""
    logging_setup._setup_logging()
    rotating = [h for h in _vt_handlers() if isinstance(h, logging.handlers.RotatingFileHandler)]
    assert len(rotating) == 1, f"expected 1 RotatingFileHandler, got {rotating}"


def test_log_file_created_under_config_dir(config_dir, clean_env, stub_side_effects):
    """The rotating log file lives at <config_dir>/voice-typer.log on disk."""
    logging_setup._setup_logging()
    assert (config_dir / "voice-typer.log").exists()


def test_handler_baseFilename_points_at_config_dir(config_dir, clean_env, stub_side_effects):
    """The RotatingFileHandler's baseFilename is <config_dir>/voice-typer.log."""
    logging_setup._setup_logging()
    rotating = next(h for h in _vt_handlers() if isinstance(h, logging.handlers.RotatingFileHandler))
    assert Path(rotating.baseFilename) == config_dir / "voice-typer.log"


def test_rotating_handler_uses_backslashreplace_errors(config_dir, clean_env, stub_side_effects):
    """The file handler escapes un-encodable Unicode (HOTKEY-CRASH fix)."""
    logging_setup._setup_logging()
    rotating = next(h for h in _vt_handlers() if isinstance(h, logging.handlers.RotatingFileHandler))
    stream = rotating.stream
    # TextIOWrapper exposes ``errors``; the underlying encoding should be utf-8
    # with backslashreplace so arrows / em-dashes survive Windows cp1252.
    assert getattr(stream, "errors", None) == "backslashreplace"


# ─── Log level ────────────────────────────────────────────────────────────


def test_default_logger_level_is_debug(config_dir, clean_env, stub_side_effects):
    """Without VOICE_TYPER_QUIET, the voice_typer logger level is DEBUG."""
    logging_setup._setup_logging()
    assert logging.getLogger("voice_typer").level == logging.DEBUG


def test_quiet_env_var_raises_level_to_warning(config_dir, clean_env, stub_side_effects, monkeypatch):
    """VOICE_TYPER_QUIET=1 raises the logger level to WARNING (PROD-020)."""
    monkeypatch.setenv("VOICE_TYPER_QUIET", "1")
    logging_setup._setup_logging()
    assert logging.getLogger("voice_typer").level == logging.WARNING


# ─── Idempotency ──────────────────────────────────────────────────────────


def test_idempotent_no_duplicate_rotating_file_handlers(config_dir, clean_env, stub_side_effects):
    """Calling _setup_logging twice does not add a second RotatingFileHandler."""
    logging_setup._setup_logging()
    before = [h for h in _vt_handlers() if isinstance(h, logging.handlers.RotatingFileHandler)]
    logging_setup._setup_logging()
    after = [h for h in _vt_handlers() if isinstance(h, logging.handlers.RotatingFileHandler)]
    assert len(before) == 1
    assert len(after) == 1, f"idempotency broken: {len(before)} -> {len(after)} RotatingFileHandlers"


def test_idempotent_total_handler_count_stable(config_dir, clean_env, stub_side_effects):
    """Total handler count does not grow across repeated calls."""
    logging_setup._setup_logging()
    n1 = len(_vt_handlers())
    logging_setup._setup_logging()
    n2 = len(_vt_handlers())
    logging_setup._setup_logging()
    n3 = len(_vt_handlers())
    assert n1 == n2 == n3, f"handler count grew: {n1} -> {n2} -> {n3}"


def test_idempotent_no_duplicate_stream_handlers_in_port_mode(config_dir, clean_env, stub_side_effects, monkeypatch):
    """In --port mode, repeated calls don't duplicate the colored stream handler."""
    monkeypatch.setattr(sys, "argv", ["voice_typer", "--port", "9999"])
    logging_setup._setup_logging()
    before = [h for h in _vt_handlers() if isinstance(h, _FlushingStreamHandler)]
    logging_setup._setup_logging()
    after = [h for h in _vt_handlers() if isinstance(h, _FlushingStreamHandler)]
    assert len(before) == 1
    assert len(after) == 1


# ─── Log output reaches disk ──────────────────────────────────────────────


def test_log_message_reaches_file(config_dir, clean_env, stub_side_effects):
    """A log.info call after _setup_logging writes the message to the log file."""
    logging_setup._setup_logging()
    lg = logging.getLogger("voice_typer.server.fake_module")
    lg.info("[HOTKEY] RegisterHotKey succeeded")
    _flush_all()
    content = (config_dir / "voice-typer.log").read_text(encoding="utf-8")
    assert "[HOTKEY] RegisterHotKey succeeded" in content


def test_session_id_bracket_appears_in_file(config_dir, clean_env, stub_side_effects):
    """The 8-char per-process session_id bracket appears in file log output."""
    logging_setup._setup_logging()
    lg = logging.getLogger("voice_typer.server.fake_module")
    lg.info("[HOTKEY] fired")
    _flush_all()
    content = (config_dir / "voice-typer.log").read_text(encoding="utf-8")
    assert re.search(r"\[[0-9a-f]{8}\]", content), f"no 8-char session_id bracket in log file:\n{content}"


# ─── Environment variable side effects ───────────────────────────────────


def test_sets_hf_home_under_config_dir(config_dir, clean_env, stub_side_effects):
    """_setup_logging redirects HF_HOME to <config_dir>/huggingface."""
    logging_setup._setup_logging()
    assert os.environ.get("HF_HOME") == str(config_dir / "huggingface")


def test_does_not_override_existing_hf_home(config_dir, clean_env, stub_side_effects, monkeypatch):
    """If HF_HOME is already set, _setup_logging leaves it alone (setdefault)."""
    monkeypatch.setenv("HF_HOME", "/pre/set/hf")
    logging_setup._setup_logging()
    assert os.environ.get("HF_HOME") == "/pre/set/hf"


# ─── Stream-handler level (only installed when --port or TTY) ────────────


def test_debug_env_var_sets_stream_handler_to_debug(config_dir, clean_env, stub_side_effects, monkeypatch):
    """VOICE_TYPER_DEBUG=1 makes the colored stream handler emit DEBUG messages."""
    monkeypatch.setenv("VOICE_TYPER_DEBUG", "1")
    monkeypatch.setattr(sys, "argv", ["voice_typer", "--port", "1"])
    logging_setup._setup_logging()
    stream = next(h for h in _vt_handlers() if isinstance(h, _FlushingStreamHandler))
    assert stream.level == logging.DEBUG


def test_default_stream_handler_level_is_info(config_dir, clean_env, stub_side_effects, monkeypatch):
    """Without VOICE_TYPER_DEBUG, the stream handler sits at INFO."""
    monkeypatch.setattr(sys, "argv", ["voice_typer", "--port", "1"])
    logging_setup._setup_logging()
    stream = next(h for h in _vt_handlers() if isinstance(h, _FlushingStreamHandler))
    assert stream.level == logging.INFO


# ─── Side-effect invocation ──────────────────────────────────────────────


def test_calls_validate_env_vars(config_dir, clean_env, stub_side_effects):
    """_setup_logging delegates env-var validation to _validate_env_vars."""
    logging_setup._setup_logging()
    stub_side_effects["validate_env"].assert_called_once_with()


def test_calls_container_warn_if_in_container(config_dir, clean_env, stub_side_effects):
    """_setup_logging invokes container detection warning."""
    logging_setup._setup_logging()
    stub_side_effects["container_warn"].assert_called_once_with()


def test_calls_install_crash_handler(config_dir, clean_env, stub_side_effects):
    """_setup_logging installs the crash handler at the end."""
    logging_setup._setup_logging()
    stub_side_effects["crash_install"].assert_called_once_with()


def test_passes_config_dir_to_crash_handler(config_dir, clean_env, stub_side_effects):
    """The crash handler is given the same config_dir returned by _config_dir()."""
    logging_setup._setup_logging()
    stub_side_effects["crash_set_dir"].assert_called_once()
    (args, _) = stub_side_effects["crash_set_dir"].call_args
    assert args[0] == config_dir


# ─── Error path ───────────────────────────────────────────────────────────


def test_raises_when_config_dir_uncreatable(tmp_path: Path, monkeypatch, clean_env, stub_side_effects):
    """When the config directory cannot be created, _setup_logging raises OSError.

    ``setup_logging`` calls ``config_dir.mkdir(parents=True, exist_ok=True)``
    and then opens a RotatingFileHandler inside it.  If the parent path is
    a file (not a directory), mkdir raises ``NotADirectoryError`` (subclass
    of ``OSError``) — the error must propagate rather than being silently
    swallowed.
    """
    blocker = tmp_path / "i_am_a_file"
    blocker.write_text("not a directory")
    bad_config_dir = blocker / "cfg"  # parent is a file → uncreatable
    monkeypatch.setattr(logging_setup, "_config_dir", lambda: bad_config_dir)
    monkeypatch.setattr(logging_setup, "_migrate_from_legacy", lambda: None)
    with pytest.raises(OSError):
        logging_setup._setup_logging()
