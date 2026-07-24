"""RW-7: regression tests for the unified path helpers.

Verifies:
1. Every helper in :mod:`voice_typer.server._paths` returns a path that
   equals :func:`voice_typer.server.config._config_dir` (or a descendant
   of it). This guarantees the helpers respect the platform-specific
   ``_config_dir()`` logic (Windows ``%APPDATA%``, macOS
   ``~/Library/Application Support``, Linux ``$XDG_DATA_HOME``, the
   ``VOICE_TYPER_CONFIG_DIR`` override, and the legacy
   ``~/.voice-typer`` migration check).

2. No module in ``voice_typer/server/`` (except ``config.py`` for the
   legacy migration probe and ``_paths.py`` itself, which is the
   canonical home for the legacy-path literal) still contains the
   pattern ``Path.home() / ".voice-typer"`` in executable code.
   Every auxiliary path (PID files, sentinel files, log files, venv
   interpreters) must route through the helpers in ``_paths.py`` so it
   respects the platform-aware resolution chain.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from voice_typer.server import _paths

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVER_DIR = REPO_ROOT / "voice_typer" / "server"


# ─── Helper: every _paths.* helper returns a path under _config_dir() ─────


class TestHelpersReturnPathsUnderConfigDir:
    """Every helper in ``_paths.py`` returns a path that is exactly
    :func:`_config_dir` or a descendant of it.

    The fixture pins ``_paths._config_dir`` (the imported reference) to
    a tmp path so the assertions are deterministic and don't depend on
    the host's filesystem state.
    """

    @pytest.fixture(autouse=True)
    def _pin_config_dir(self, tmp_path: Path, monkeypatch):
        """Pin ``_paths._config_dir`` to a tmp path for the duration of
        each test.

        We patch ``_paths._config_dir`` (the imported reference inside
        ``_paths``) rather than ``config._config_dir`` so we exercise
        the helpers' actual delegation chain — every helper should call
        ``_config_dir()`` (the imported function) at least once.
        """
        monkeypatch.setattr(_paths, "_config_dir", lambda: tmp_path)
        self.dir = tmp_path

    def test_config_dir_equals_config_dir(self):
        assert _paths.config_dir() == self.dir

    def test_prewarm_launchagent_log_under_config_dir(self):
        assert _paths.prewarm_launchagent_log() == self.dir / "prewarm-launchagent.log"

    def test_autostart_log_under_config_dir(self):
        assert _paths.autostart_log() == self.dir / "autostart.log"

    @pytest.mark.parametrize(
        "platform,expected_subpath",
        [
            ("win32", Path("venv") / "Scripts" / "pythonw.exe"),
            ("darwin", Path("venv") / "bin" / "python"),
            ("linux", Path("venv") / "bin" / "python"),
        ],
    )
    def test_venv_pythonw_under_config_dir(self, monkeypatch, platform: str, expected_subpath: Path):
        """``_paths.venv_pythonw()`` returns the platform-appropriate
        venv interpreter under ``_config_dir()``."""
        monkeypatch.setattr(_paths.sys, "platform", platform)
        assert _paths.venv_pythonw() == self.dir / expected_subpath

    def test_legacy_hf_cache_dir_not_under_config_dir(self):
        """``_paths.legacy_hf_cache_dir()`` is the ONE exception — it
        intentionally returns the literal ``Path.home() /
        ".voice-typer" / "huggingface"`` (NOT under ``_config_dir()``)
        because it's the defensive fallback used when
        ``_config_dir()`` itself fails (the BootTrigger scenario
        where ``$HOME`` / ``%USERPROFILE%`` are unset).

        The autouse ``_pin_config_dir`` fixture patches
        ``_paths._config_dir`` to a tmp path, but this helper must NOT
        consult it — it calls ``Path.home()`` directly so it still
        works when ``_config_dir()`` raises.
        """
        expected = Path.home() / ".voice-typer" / "huggingface"
        assert _paths.legacy_hf_cache_dir() == expected
        # And confirm the pinned _config_dir is NOT what's returned
        # (i.e. the helper doesn't accidentally delegate to it).
        assert _paths.legacy_hf_cache_dir() != self.dir


# ─── Regression: no hardcoded Path.home() / ".voice-typer" ────────────────


# Files allowed to reference the legacy ``~/.voice-typer`` path directly:
#   - ``_paths.py``: the canonical home for the legacy-path literal
#     (``legacy_hf_cache_dir()`` defensive fallback, plus docstring
#     references explaining the migration).
#   - ``config.py``: the legacy migration probe inside ``_config_dir()``
#     itself — this IS the canonical legacy-path check that decides
#     whether to migrate an existing ``~/.voice-typer`` install to the
#     platform-specific location.
_ALLOWED_FILES = {"_paths.py", "config.py"}


def _strip_docstrings(source: str, filename: str) -> list[str]:
    """Return the source lines with docstrings replaced by blank lines.

    Uses :mod:`ast` to identify the line ranges of module / class /
    function docstrings (the first ``ast.Expr`` whose value is a string
    constant), then returns the source as a list of lines with those
    line ranges blanked out. This lets the grep-based regression test
    ignore docstring references to the old ``Path.home() /
    ".voice-typer"`` convention (which are documentation, not code).

    Pure ``#`` comment lines are also blanked out by the caller.
    """
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError:
        # If the file doesn't parse, return the raw lines — the test
        # will then flag any matches (intentionally conservative).
        return source.splitlines()

    lines = source.splitlines()
    blanked = list(lines)

    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not node.body:
                continue
            first = node.body[0]
            if not isinstance(first, ast.Expr):
                continue
            value = first.value
            # ast.Constant covers Python 3.8+ (the only form produced by
            # modern parsers). We additionally require its value to be a
            # str so we don't blank out non-string expression statements.
            if not isinstance(value, ast.Constant):
                continue
            if not isinstance(value.value, str):
                continue
            # `lineno` is 1-indexed; convert to 0-indexed for slicing.
            start = first.lineno - 1
            end = first.end_lineno  # 1-indexed inclusive → exclusive
            for i in range(start, end):
                if 0 <= i < len(blanked):
                    blanked[i] = ""
    return blanked


_LEGACY_PATH_PATTERN = re.compile(r'Path\.home\(\)\s*/\s*"\.voice-typer"')


class TestNoHardcodedVoiceTyperPaths:
    """RW-7: no module in ``voice_typer/server/`` (except ``config.py``
    for the legacy migration probe and ``_paths.py`` itself, which is
    the canonical home for the legacy-path literal) still contains the
    pattern ``Path.home() / ".voice-typer"`` in executable code.

    Every auxiliary path (PID files, sentinel files, log files, venv
    interpreters) must route through the helpers in ``_paths.py`` so
    it respects the platform-aware ``_config_dir()`` resolution chain
    (Windows ``%APPDATA%``, macOS ``~/Library/Application Support``,
    Linux ``$XDG_DATA_HOME``, the ``VOICE_TYPER_CONFIG_DIR`` override,
    and the legacy ``~/.voice-typer`` migration check).

    Docstring references and ``#`` comments that mention the old
    convention (for documentation / migration context) are allowed —
    only executable code is flagged.
    """

    def test_no_hardcoded_paths_in_server_modules(self):
        offenders: list[str] = []
        py_files = sorted(SERVER_DIR.rglob("*.py"))
        # Sanity check: the test should examine at least the modules
        # we know were refactored, otherwise the test silently passes
        # if SERVER_DIR is wrong.
        #
        # The required-list uses basenames that are unique across the
        # server tree. ``prewarm.py`` and ``server_platform.py`` were
        # reorganized into packages (``prewarm/__init__.py`` and
        # ``server_platform/__init__.py``) — the package layout means
        # ``rglob("*.py")`` returns ``__init__.py`` (whose basename
        # collides across packages), so we instead anchor on a
        # representative non-init module inside each package plus the
        # top-level files that were the original refactor targets.
        examined_names = {p.name for p in py_files}
        examined_rel = {str(p.relative_to(SERVER_DIR)).replace("\\", "/") for p in py_files}
        required_basenames = (
            "config.py",
            "_paths.py",
            "autostart_launcher.py",
            "prewarm_scheduler_posix.py",
            "task_scheduler.py",
            "duck_crash_recovery.py",
        )
        for required in required_basenames:
            assert required in examined_names, (
                f"RW-7 test setup error: {required} not found under "
                f"{SERVER_DIR} — the test cannot verify the regression "
                "without examining the refactored modules"
            )
        # Package-layout sanity: ensure rglob descended into both the
        # ``prewarm/`` and ``server_platform/`` sub-packages (the
        # pre-refactor ``prewarm.py`` and ``server_platform.py`` were
        # split into these packages; if rglob missed them the test
        # would silently skip every file inside them).
        for required_pkg_file in (
            "prewarm/paths.py",
            "prewarm/cache_probe.py",
            "prewarm/logging_setup.py",
            "server_platform/autostart.py",
            "server_platform/autostart_macos.py",
            "server_platform/desktop_shortcut.py",
        ):
            assert required_pkg_file in examined_rel, (
                f"RW-7 test setup error: {required_pkg_file} not found "
                f"under {SERVER_DIR} — rglob did not descend into the "
                "prewarm/ or server_platform/ packages where the legacy "
                "path-literal refactor lives"
            )

        for py_file in py_files:
            if py_file.name in _ALLOWED_FILES:
                continue
            try:
                source = py_file.read_text(encoding="utf-8")
            except OSError:
                continue
            # Strip docstrings (they often reference the old convention
            # for migration / documentation context).
            lines = _strip_docstrings(source, str(py_file))
            for line_num, line in enumerate(lines, 1):
                # Skip pure comment lines (documentation often mentions
                # the old path in prose).
                if line.lstrip().startswith("#"):
                    continue
                if _LEGACY_PATH_PATTERN.search(line):
                    offenders.append(f"{py_file.relative_to(REPO_ROOT)}:{line_num}: {line.rstrip()}")
        assert not offenders, (
            "RW-7 regression: hardcoded Path.home() / '.voice-typer' "
            "found in executable code. Use voice_typer.server._paths "
            "helpers instead (config_dir, prewarm_launchagent_log, "
            "autostart_log, venv_pythonw, legacy_hf_cache_dir):\n" + "\n".join(offenders)
        )

    def test_config_py_still_has_legacy_migration_probe(self):
        """``config.py`` must retain its ``legacy = Path.home() /
        ".voice-typer"`` migration probe — it's the canonical legacy-
        path check that decides whether to migrate an existing
        ``~/.voice-typer`` install to the platform-specific location.
        Removing it would break migration for existing users."""
        config_py = SERVER_DIR / "config.py"
        source = config_py.read_text(encoding="utf-8")
        lines = _strip_docstrings(source, str(config_py))
        found = False
        for line in lines:
            if line.lstrip().startswith("#"):
                continue
            if _LEGACY_PATH_PATTERN.search(line):
                found = True
                break
        assert found, (
            "RW-7: config.py must retain its legacy migration probe "
            "('legacy = Path.home() / \".voice-typer\"') — removing it "
            "would break migration for existing ~/.voice-typer installs"
        )

    def test_paths_py_has_legacy_hf_cache_dir(self):
        """``_paths.py`` must define ``legacy_hf_cache_dir()`` (the
        defensive fallback that returns ``Path.home() / ".voice-typer"
        / "huggingface"``) so prewarm.py has somewhere to delegate its
        last-resort fallback to."""
        assert hasattr(_paths, "legacy_hf_cache_dir"), (
            "RW-7: _paths.legacy_hf_cache_dir must exist (prewarm.py "
            "delegates its BootTrigger defensive fallback to it)"
        )
        assert _paths.legacy_hf_cache_dir() == (Path.home() / ".voice-typer" / "huggingface")
