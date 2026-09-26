"""regression tests for the unified path helpers."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from voice_typer.server import _paths

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVER_DIR = REPO_ROOT / "voice_typer" / "server"


class TestHelpersReturnPathsUnderConfigDir:
    """Every helper in ``_paths.py`` returns a path that is exactly"""

    @pytest.fixture(autouse=True)
    def _pin_config_dir(self, tmp_path: Path, monkeypatch):
        """each test."""
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
        """``_paths.venv_pythonw()`` returns the platform-appropriate"""
        monkeypatch.setattr(_paths.sys, "platform", platform)
        assert _paths.venv_pythonw() == self.dir / expected_subpath

    def test_legacy_hf_cache_dir_not_under_config_dir(self):
        """
        ``_paths.legacy_hf_cache_dir()`` is the ONE exception, it
        ``_paths._config_dir`` to a tmp path, but this helper must NOT
        """
        expected = Path.home() / ".lausu" / "huggingface"
        assert _paths.legacy_hf_cache_dir() == expected
        # And confirm the pinned _config_dir is NOT what's returned
        assert _paths.legacy_hf_cache_dir() != self.dir


# Files allowed to reference the legacy ``~/.lausu`` path directly:
_ALLOWED_FILES = {"_paths.py", "__init__.py"}
_ALLOWED_REL_PATHS = {"config/__init__.py", "config/_accessors.py"}


def _strip_docstrings(source: str, filename: str) -> list[str]:
    """Return the source lines with docstrings replaced by blank lines."""
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError:
        # If the file doesn't parse, return the raw lines, the test
        return source.splitlines()

    lines = source.splitlines()
    blanked = list(lines)

    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            if not node.body:
                continue
            first = node.body[0]
            if not isinstance(first, ast.Expr):
                continue
            value = first.value
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


_LEGACY_PATH_PATTERN = re.compile(r'Path\.home\(\)\s*/\s*"\.lausu"')


class TestNoHardcodedLausuPaths:
    """no module in ``voice_typer/server/`` (except"""

    def test_no_hardcoded_paths_in_server_modules(self):
        offenders: list[str] = []
        py_files = sorted(SERVER_DIR.rglob("*.py"))
        # Sanity check: the test should examine at least the modules
        examined_names = {p.name for p in py_files}
        examined_rel = {str(p.relative_to(SERVER_DIR)).replace("\\", "/") for p in py_files}
        required_basenames = (
            "_paths.py",
            "autostart_launcher.py",
            "task_scheduler.py",
            "duck_crash_recovery.py",
        )
        for required in required_basenames:
            assert required in examined_names, (
                f"test setup error: {required} not found under "
                f"{SERVER_DIR}, the test cannot verify the regression "
                "without examining the refactored modules"
            )
        for required_config_file in ("config/__init__.py", "config/_accessors.py"):
            assert required_config_file in examined_rel, (
                f"test setup error: {required_config_file} not found under "
                f"{SERVER_DIR}, the test cannot verify the legacy migration "
                "probe without examining the refactored config package"
            )
        for required_pkg_file in (
            "prewarm/cache_probe.py",
            "server_platform/autostart.py",
            "server_platform/autostart_macos.py",
            "server_platform/desktop_shortcut.py",
        ):
            assert required_pkg_file in examined_rel, (
                f"test setup error: {required_pkg_file} not found "
                f"under {SERVER_DIR}, rglob did not descend into the "
                "prewarm/ or server_platform/ packages where the legacy "
                "path-literal refactor lives"
            )

        for py_file in py_files:
            # Allow the canonical homes for the legacy-path literal:
            rel_to_server = str(py_file.relative_to(SERVER_DIR)).replace("\\", "/")
            if py_file.name in _ALLOWED_FILES or rel_to_server in _ALLOWED_REL_PATHS:
                continue
            try:
                source = py_file.read_text(encoding="utf-8")
            except OSError:
                continue
            lines = _strip_docstrings(source, str(py_file))
            for line_num, line in enumerate(lines, 1):
                # Skip pure comment lines (documentation often mentions
                if line.lstrip().startswith("#"):
                    continue
                if _LEGACY_PATH_PATTERN.search(line):
                    offenders.append(f"{py_file.relative_to(REPO_ROOT)}:{line_num}: {line.rstrip()}")
        assert not offenders, (
            "regression: hardcoded Path.home() / '.lausu' "
            "found in executable code. Use voice_typer.server._paths "
            "helpers instead (config_dir, prewarm_launchagent_log, "
            "autostart_log, venv_pythonw, legacy_hf_cache_dir):\n" + "\n".join(offenders)
        )

    def test_config_py_still_has_legacy_migration_probe(self):
        """``config/_accessors.py`` (the config-package module that now"""
        accessors_py = SERVER_DIR / "config" / "_accessors.py"
        source = accessors_py.read_text(encoding="utf-8")
        lines = _strip_docstrings(source, str(accessors_py))
        found = False
        for line in lines:
            if line.lstrip().startswith("#"):
                continue
            if _LEGACY_PATH_PATTERN.search(line):
                found = True
                break
        assert found, (
            "config/_accessors.py must retain its legacy migration "
            "probe ('legacy = Path.home() / \".lausu\"'), "
            "removing it would break migration for existing "
            "~/.lausu installs"
        )

    def test_paths_py_has_legacy_hf_cache_dir(self):
        """defensive fallback that returns ``Path.home() / \".lausu\""""
        assert hasattr(_paths, "legacy_hf_cache_dir"), (
            "_paths.legacy_hf_cache_dir must exist (prewarm.py delegates its BootTrigger defensive fallback to it)"
        )
        assert _paths.legacy_hf_cache_dir() == (Path.home() / ".lausu" / "huggingface")
