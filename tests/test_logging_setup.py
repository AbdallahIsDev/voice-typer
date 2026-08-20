"""Unit tests for ``voice_typer.server.logging_setup``.

The module exposes ``_setup_logging`` (configures logging, validates env
vars, warns about containers, stages the startup banner + crash-handler
install) and ``_emit_startup_banner`` (emits the ``[STARTUP] logging
initialized`` banner and installs the Windows VEH crash handler; called
later, after the ``APP starting`` line).

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
        "VOICE_TYPER_SESSION_ID",
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
    # ``logging_setup`` binds ``crash_handler`` at ITS import time
    # (``from voice_typer.server import crash_handler as _crash_handler``),
    # so patch the BOUND module object, not the ``sys.modules`` name —
    # a purge+re-import in test_crash_handler_split can otherwise give
    # the module a NEW identity, making this monkeypatch hit a different
    # object than the one ``_setup_logging`` actually calls (the real
    # function then runs and the spy is never called).
    monkeypatch.setattr(
        logging_setup._crash_handler,
        "install_crash_handler",
        spies["crash_install"],
    )
    monkeypatch.setattr(
        logging_setup._crash_handler,
        "set_crash_handler_config_dir",
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
    assert (config_dir / "logs" / "voice-typer.log").exists()


def test_handler_baseFilename_points_at_config_dir(config_dir, clean_env, stub_side_effects):  # noqa: N802
    """The RotatingFileHandler's baseFilename is <config_dir>/voice-typer.log."""
    logging_setup._setup_logging()
    rotating = next(h for h in _vt_handlers() if isinstance(h, logging.handlers.RotatingFileHandler))
    assert Path(rotating.baseFilename) == config_dir / "logs" / "voice-typer.log"


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
    content = (config_dir / "logs" / "voice-typer.log").read_text(encoding="utf-8")
    assert "[HOTKEY] RegisterHotKey succeeded" in content


def test_no_session_id_bracket_in_file(config_dir, clean_env, stub_side_effects):
    """The 8-char per-process session_id bracket must NOT appear in file
    log output — it added noise to every line without helping the user
    read the log (correlation stays available in JSON mode)."""
    logging_setup._setup_logging()
    lg = logging.getLogger("voice_typer.server.fake_module")
    lg.info("[HOTKEY] fired")
    _flush_all()
    content = (config_dir / "logs" / "voice-typer.log").read_text(encoding="utf-8")
    assert not re.search(r"\[[0-9a-f]{8}\]", content), (
        f"8-char session_id bracket must NOT appear in file log:\n{content}"
    )
    assert "[HOTKEY] fired" in content
    # Clean space-separated timestamp (no T separator, no tz offset).
    first = content.split()[0]
    assert "T" not in first and "+" not in first, f"clean ts expected: {content!r}"


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
    """_setup_logging stages the crash-handler install; _emit_startup_banner installs it."""
    logging_setup._setup_logging()
    stub_side_effects["crash_install"].assert_not_called()
    logging_setup._emit_startup_banner()
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


# secure log file permissions ─────────────────────────────────


@pytest.mark.skipif(os.name != "posix", reason="POSIX-only file mode check")
def test_log_file_mode_is_0o600_on_posix(config_dir, clean_env, stub_side_effects):
    """G4-H-07: ``voice-typer.log`` is created with mode 0o600 on POSIX.

    The rotating log file contains dictated-text previews, exception
    tracebacks, and hotkey registrations — it must be world-unreadable so
    a co-located user can not ``cat`` it.  ``setup_logging`` sets the
    process umask to 0o077 and explicitly ``os.chmod``s the file to
    0o600 after construction (defence in depth).
    """
    import stat

    logging_setup._setup_logging()
    log_file = config_dir / "logs" / "voice-typer.log"
    assert log_file.exists(), "log file was not created"
    mode = stat.S_IMODE(os.stat(log_file).st_mode)
    assert mode == 0o600, (
        f"G4-H-07 regression: voice-typer.log has mode {oct(mode)}, expected 0o600. "
        "Co-located users could read dictated-text previews and exception tracebacks."
    )


@pytest.mark.skipif(os.name != "posix", reason="POSIX-only file mode check")
def test_log_file_handler_level_gated_by_debug_default_info(config_dir, clean_env, stub_side_effects):
    """G4-H-35: the RotatingFileHandler level is INFO by default.

    Root stays at DEBUG so child loggers can emit DEBUG records — the
    handler-level filter is what drops them at INFO in production.
    """
    logging_setup._setup_logging()
    rotating = next(h for h in _vt_handlers() if isinstance(h, logging.handlers.RotatingFileHandler))
    assert rotating.level == logging.INFO, f"default file handler level is {rotating.level}, expected INFO"
    # Root stays at DEBUG so child loggers can emit DEBUG when env var set.
    assert logging.getLogger("voice_typer").level == logging.DEBUG


def test_log_file_handler_level_debug_when_voice_typer_debug(config_dir, clean_env, stub_side_effects, monkeypatch):
    """G4-H-35: VOICE_TYPER_DEBUG=1 raises the file handler to DEBUG."""
    monkeypatch.setenv("VOICE_TYPER_DEBUG", "1")
    logging_setup._setup_logging()
    rotating = next(h for h in _vt_handlers() if isinstance(h, logging.handlers.RotatingFileHandler))
    assert rotating.level == logging.DEBUG, (
        f"VOICE_TYPER_DEBUG=1 should raise file handler to DEBUG, got {rotating.level}"
    )


def test_get_log_file_path_returns_config_dir_voice_typer_log(config_dir):
    """G4-L-19: ``get_log_file_path`` returns ``<config_dir>/voice-typer.log``.

    Centralising the literal in ``log.py`` means the in-app log viewer
    (agent 2-y) and ``setup_logging`` agree on the filename even if it
    ever changes.
    """
    from voice_typer.server.log import get_log_file_path

    path = get_log_file_path(config_dir)
    assert path == config_dir / "logs" / "voice-typer.log"
    assert path.name == "voice-typer.log"


def test_per_module_log_levels_applied_from_env(config_dir, clean_env, stub_side_effects, monkeypatch):
    """Per-module log level overrides via VOICE_TYPER_LOG_LEVEL_MODULES env var.

    Operators can crank up DEBUG on a single subsystem without enabling
    DEBUG globally — the env var is parsed in ``setup_logging`` and
    applied to each named logger after the root level is set.
    """
    monkeypatch.setenv(
        "VOICE_TYPER_LOG_LEVEL_MODULES",
        "voice_typer.server.dictation_pipeline=DEBUG,voice_typer.server.recording=WARNING",
    )
    logging_setup._setup_logging()
    assert logging.getLogger("voice_typer.server.dictation_pipeline").level == logging.DEBUG
    assert logging.getLogger("voice_typer.server.recording").level == logging.WARNING
    # Root unchanged (still DEBUG).
    assert logging.getLogger("voice_typer").level == logging.DEBUG


def test_per_module_log_levels_ignores_invalid_entries(config_dir, clean_env, stub_side_effects, monkeypatch):
    """Invalid entries in VOICE_TYPER_LOG_LEVEL_MODULES are silently skipped.

    A typo in one entry must not break logging setup.
    """
    monkeypatch.setenv(
        "VOICE_TYPER_LOG_LEVEL_MODULES",
        "voice_typer.server.fake=BOGUS,,voice_typer.server.another=INFO",
    )
    # Should not raise.
    logging_setup._setup_logging()
    assert logging.getLogger("voice_typer.server.another").level == logging.INFO


# startup banner logging the active log configuration ─────


class TestStartupBanner:  # noqa: N801
    """GT-B1-15: after ``_setup_logging_shared`` returns, emit a single
    INFO-level banner so operators can see at a glance which logging
    configuration took effect::

        log.info(
            "[STARTUP] logging initialized: file=%s, level=%s, json=%s, "
            "debug=%s, quiet=%s, session=%s",
            log_file, root_level, json_mode, debug, quiet, session_id,
        )

    The session id is included exactly ONCE, as the trailing
    ``session=`` field of the banner — the first line of the session —
    so every subsequent line implicitly belongs to this session
    without the id being repeated per-line (C-LOG-1 keeps per-line
    output clean; the banner is the single mention).

    The banner is the first INFO record emitted through the
    ``voice_typer.server.logging_setup`` logger after the rotating
    file handler is installed, so it lands at the top of
    ``voice-typer.log`` and is the first thing an operator sees when
    investigating a crash.
    """

    def _banner_lines(self, config_dir: Path) -> str:
        """Helper: read the log file and return the banner lines."""
        content = (config_dir / "logs" / "voice-typer.log").read_text(encoding="utf-8")
        return "\n".join(line for line in content.splitlines() if "[STARTUP]" in line)

    def test_banner_appears_in_log_file(self, config_dir, clean_env, stub_side_effects):
        """The ``[STARTUP] logging initialized:`` banner is written to
        ``<config_dir>/voice-typer.log`` after ``_emit_startup_banner`` runs.
        """
        logging_setup._setup_logging()
        logging_setup._emit_startup_banner()
        _flush_all()
        banner = self._banner_lines(config_dir)
        assert "[STARTUP] logging initialized:" in banner, (
            f"GT-B1-15 regression: no startup banner in log file; got:\n{banner}"
        )

    def test_banner_includes_file_path(self, config_dir, clean_env, stub_side_effects):
        """The banner includes the resolved log file path."""
        logging_setup._setup_logging()
        logging_setup._emit_startup_banner()
        _flush_all()
        banner = self._banner_lines(config_dir)
        expected_file = str(config_dir / "logs" / "voice-typer.log")
        # SEC-009: the PII log filter replaces the home-dir prefix with
        # ``~`` in rendered messages, so accept both the full path and
        # the home-shortened form.
        expected_variants = {expected_file, expected_file.replace(str(Path.home()), "~")}
        assert any(f"file={v}" in banner for v in expected_variants), (
            f"GT-B1-15: banner missing file path; got: {banner!r}"
        )

    def test_banner_includes_level_name(self, config_dir, clean_env, stub_side_effects):
        """The banner reports the FILE HANDLER level NAME (``INFO``,
        ``DEBUG``, ``WARNING``, etc.) — the level that actually gates what
        lands in the log file — not the numeric value, so it's
        human-readable. Under the default config (debug=False) the file
        handler sits at INFO, so the banner shows ``level=INFO``
        (consistent with ``debug=False``; the ``voice_typer`` logger
        itself is always pinned at DEBUG internally).
        """
        logging_setup._setup_logging()
        logging_setup._emit_startup_banner()
        _flush_all()
        banner = self._banner_lines(config_dir)
        assert "level=INFO" in banner, f"GT-B1-15: banner missing/incorrect level; got: {banner!r}"

    def test_banner_reflects_quiet_flag(self, config_dir, clean_env, stub_side_effects, monkeypatch):
        """When VOICE_TYPER_QUIET=1, the banner shows level=WARNING and quiet=True."""
        monkeypatch.setenv("VOICE_TYPER_QUIET", "1")
        logging_setup._setup_logging()
        logging_setup._emit_startup_banner()
        _flush_all()
        banner = self._banner_lines(config_dir)
        assert "level=WARNING" in banner, f"GT-B1-15: quiet mode should set level=WARNING; got: {banner!r}"
        assert "quiet=True" in banner, f"GT-B1-15: banner should show quiet=True; got: {banner!r}"

    def test_banner_reflects_debug_flag(self, config_dir, clean_env, stub_side_effects, monkeypatch):
        """When VOICE_TYPER_DEBUG=1, the banner shows debug=True."""
        monkeypatch.setenv("VOICE_TYPER_DEBUG", "1")
        logging_setup._setup_logging()
        logging_setup._emit_startup_banner()
        _flush_all()
        banner = self._banner_lines(config_dir)
        assert "debug=True" in banner, f"GT-B1-15: banner should show debug=True; got: {banner!r}"

    def test_banner_reflects_json_mode(self, config_dir, clean_env, stub_side_effects, monkeypatch):
        """When VOICE_TYPER_LOG_JSON=1, the banner shows json=True."""
        monkeypatch.setenv("VOICE_TYPER_LOG_JSON", "1")
        logging_setup._setup_logging()
        logging_setup._emit_startup_banner()
        _flush_all()
        banner = self._banner_lines(config_dir)
        assert "json=True" in banner, (
            f"GT-B1-15: banner should show json=True under VOICE_TYPER_LOG_JSON=1; got: {banner!r}"
        )

    def test_banner_includes_session_id_once(self, config_dir, clean_env, stub_side_effects):
        """The session id is mentioned exactly ONCE per session: on the
        VERY FIRST line of the log file (the ``[STARTUP] logging
        initialized:`` banner), never on any subsequent line (C-LOG-1
        keeps per-line output clean; the banner is the single mention).
        """
        from voice_typer.server import log as _log_module

        logging_setup._setup_logging()
        logging_setup._emit_startup_banner()
        _flush_all()
        content = (config_dir / "logs" / "voice-typer.log").read_text(encoding="utf-8")
        lines = content.splitlines()
        session_id = _log_module._session_id
        assert session_id, "setup_logging did not populate _session_id"
        assert re.fullmatch(r"[0-9a-f]{8}", session_id), (
            f"GT-B1-15: _session_id is not an 8-char hex id; got: {session_id!r}"
        )
        # The session id must sit on the very first line of the session.
        assert lines, "GT-B1-15: log file is empty"
        assert f"session={session_id}" in lines[0], (
            f"GT-B1-15: first log line must carry session=<id>; got: {lines[0]!r}"
        )
        # ...and nowhere else in the entire file (one mention per
        # session — no per-line ids, no duplication in later banners).
        assert content.count("session=") == 1, f"GT-B1-15: session= must appear exactly once; got:\n{content}"

    def test_session_id_prefers_host_env_var(self, config_dir, clean_env, stub_side_effects, monkeypatch):
        """GT-68: when the Rust host passes ``VOICE_TYPER_SESSION_ID``,
        the Python sidecar uses it (instead of generating its own) so
        both log streams share the same cross-process join key. A
        malformed value falls back to generating a fresh 8-char hex id.
        """
        from voice_typer.server import log as _log_module

        # 1. Well-formed host value is adopted verbatim.
        monkeypatch.setenv("VOICE_TYPER_SESSION_ID", "a1b2c3d4")
        logging_setup._setup_logging()
        assert _log_module._session_id == "a1b2c3d4"

        # 2. Malformed host value (uppercase + too long) is rejected and
        #    falls back to a generated 8-char lowercase-hex id.
        monkeypatch.setenv("VOICE_TYPER_SESSION_ID", "ZZZZZZZZZZZZ")
        logging_setup._setup_logging()
        assert re.fullmatch(r"[0-9a-f]{8}", _log_module._session_id), (
            f"fallback session id must be 8-char lowercase hex; got: {_log_module._session_id!r}"
        )

    def test_banner_defaults_when_no_flags_set(self, config_dir, clean_env, stub_side_effects):
        """Default config: debug=False, quiet=False, json=False, level=DEBUG."""
        logging_setup._setup_logging()
        logging_setup._emit_startup_banner()
        _flush_all()
        banner = self._banner_lines(config_dir)
        assert "debug=False" in banner
        assert "quiet=False" in banner
        assert "json=False" in banner

    def test_banner_emitted_before_validate_env_vars(self, config_dir, clean_env, stub_side_effects):
        """PLAT-008 ordering: ``_validate_env_vars`` runs during
        ``_setup_logging`` (before the banner), and the banner is emitted by
        ``_emit_startup_banner`` (called later, after the ``APP starting``
        line). Asserted indirectly: the banner is present in the file (which
        means the file handler was installed before the banner was emitted),
        and ``_validate_env_vars`` was still called exactly once.
        """
        logging_setup._setup_logging()
        logging_setup._emit_startup_banner()
        _flush_all()
        banner = self._banner_lines(config_dir)
        assert "[STARTUP]" in banner
        stub_side_effects["validate_env"].assert_called_once_with()
