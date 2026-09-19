"""H1 + H2 tests: API.md config-table accuracy and Windows notepad behavior."""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tests.fixtures.app_helpers import make_voice_typer_app


def _api_md_path() -> Path:
    """Return the absolute path to ``docs/API.md``."""
    return Path(__file__).resolve().parent.parent / "docs" / "API.md"


def _parse_api_config_table(api_md_text: str) -> list[tuple[str, str, str, str]]:
    """Parse the \"Key Configuration Keys\" markdown table from API.md."""
    # Anchor on the heading + the table header row so we don't
    pattern = re.compile(
        r"### Key Configuration Keys\n"
        r".*?"  # optional prose between heading and table
        r"\| Key \| Type \| Default \| Description \|\n"
        r"\|[-| ]+\|\n"
        r"(?P<rows>(?:\|[^\n]+\n)+)",
        re.DOTALL,
    )
    match = pattern.search(api_md_text)
    assert match is not None, (
        "Could not locate the '### Key Configuration Keys' table in "
        "docs/API.md, did the heading or table header change?"
    )

    rows: list[tuple[str, str, str, str]] = []
    for line in match.group("rows").splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        # ``strip("|")`` removes leading/trailing pipes; ``split("|")``
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) != 4:
            continue
        key, type_str, default_str, description = cells
        # Skip the separator row (``-----|------|---------|-------------``)
        if not key or key.startswith("-"):
            continue

        # API.md wraps identifiers and literals in backticks for monospace
        def _strip_outer_backticks(s: str) -> str:
            if len(s) >= 2 and s[0] == "`" and s[-1] == "`":
                return s[1:-1]
            return s

        key = _strip_outer_backticks(key)
        type_str = _strip_outer_backticks(type_str)
        default_str = _strip_outer_backticks(default_str)
        rows.append((key, type_str, default_str, description))
    return rows


def _parse_default(default_str: str, type_str: str) -> object:
    """Coerce a documented default string to the corresponding Python value."""
    if type_str == "bool":
        if default_str == "True":
            return True
        if default_str == "False":
            return False
        raise AssertionError(f"Cannot parse bool default {default_str!r} (expected 'True' or 'False')")
    if type_str == "int":
        return int(default_str)
    if type_str == "float":
        return float(default_str)
    if type_str == "str":
        # Strip surrounding double quotes.  We don't allow single-quoted
        if len(default_str) >= 2 and default_str[0] == '"' and default_str[-1] == '"':
            return default_str[1:-1]
        raise AssertionError(f"str default {default_str!r} must be double-quoted in API.md")
    raise AssertionError(f"Unknown type {type_str!r} for default {default_str!r}")


class TestApiDocConfigTableAccuracy:
    """H1: every row in API.md's config table must match Config's defaults."""

    def test_api_md_file_exists(self):
        """Sanity: the API.md file is where we expect it."""
        path = _api_md_path()
        assert path.exists(), f"docs/API.md not found at {path}"

    def test_config_table_is_present_and_has_rows(self):
        """The 'Key Configuration Keys' table must exist and have ≥1 row."""
        rows = _parse_api_config_table(_api_md_path().read_text(encoding="utf-8"))
        assert len(rows) >= 1, "Config table has no data rows"

    def test_documented_fields_exist_on_config_with_documented_defaults(self):
        """Every (key, default) in the table must match ``Config()``."""
        from voice_typer.server.config import Config

        rows = _parse_api_config_table(_api_md_path().read_text(encoding="utf-8"))
        defaults = Config()

        failures: list[str] = []
        for key, type_str, default_str, _desc in rows:
            if not hasattr(defaults, key):
                failures.append(
                    f"  - Field {key!r} is documented in API.md but does not "
                    f"exist on Config (typo, or field was removed?)"
                )
                continue
            actual = getattr(defaults, key)
            try:
                expected = _parse_default(default_str, type_str)
            except AssertionError as exc:
                failures.append(f"  - Field {key!r}: {exc}")
                continue
            if actual != expected:
                failures.append(
                    f"  - Field {key!r}: API.md documents default {expected!r} "
                    f"({type_str}), but Config() has {actual!r} ({type(actual).__name__})"
                )

        if failures:
            pytest.fail(
                "API.md config table is out of sync with Config defaults:\n"
                + "\n".join(failures)
                + "\n\nFix: update docs/API.md to match Config in "
                "voice_typer/server/config.py."
            )

    def test_no_removed_fields_leaked_back_into_table(self):
        """Removed/renamed fields must NOT reappear in the table."""
        rows = _parse_api_config_table(_api_md_path().read_text(encoding="utf-8"))
        documented_keys = {row[0] for row in rows}
        removed = {
            "paste_enabled",  # renamed to paste_on_stop
            "clipboard_clear_delay_seconds",  # removed per ADR-0010 §8.2
            "check_updates",  # never existed on Config
            "model",  # renamed to model_size
        }
        leaked = documented_keys & removed
        assert not leaked, (
            f"Removed/renamed fields reappeared in API.md config table: {leaked}. "
            f"These were called out as stale in d-review Finding 3, do NOT re-add."
        )

    def test_recording_mode_enum_matches_validator(self):
        """The recording_mode description must list the real enum values."""
        rows = _parse_api_config_table(_api_md_path().read_text(encoding="utf-8"))
        recording_mode_row = next((r for r in rows if r[0] == "recording_mode"), None)
        assert recording_mode_row is not None, "recording_mode row missing from table"
        _key, _type, _default, desc = recording_mode_row
        assert "voice_activity" not in desc, (
            "API.md still lists 'voice_activity' as a valid recording_mode, "
            "this value was never implemented. The enum is {toggle, push_to_talk} only."
        )
        # Both real enum values must be advertised.
        assert "toggle" in desc and "push_to_talk" in desc, (
            "API.md recording_mode description must list both 'toggle' and "
            f"'push_to_talk' (the actual enum). Got: {desc!r}"
        )


# ─── H2: Windows _open_config_file, default-app open, validated notepad fallback ─


class TestWindowsOpenConfigFile:
    """XPLAT-01 + SEC-audit-011: Windows _open_config_file opens the user's"""

    def test_opens_with_default_app_first_when_associated(self, tmp_config_dir, monkeypatch):
        """Primary path uses the default-app open (association-respecting)."""
        app = make_voice_typer_app(tmp_config_dir, monkeypatch)
        monkeypatch.setattr("voice_typer.server.platform_utils.is_windows", lambda: True)
        monkeypatch.setattr("voice_typer.server.platform_utils.is_macos", lambda: False)
        monkeypatch.setattr("voice_typer.server.platform_utils.is_linux", lambda: False)

        open_calls: list = []
        monkeypatch.setattr(
            "voice_typer.server.app._windows_open_with_default_app",
            lambda path: open_calls.append(path) or 123,
        )
        monkeypatch.setattr("voice_typer.server.app._windows_wait_for_process_exit", lambda h: None)
        monkeypatch.setattr("voice_typer.server.app._windows_close_process_handle", lambda h: None)

        popen_calls: list = []

        def _record_popen(*a, **kw):
            # Filter out library-init noise (e.g. `ldconfig -p` spawned by
            cmd = a[0] if a else kw.get("args")
            if isinstance(cmd, list | tuple) and cmd and "ldconfig" in str(cmd[0]):
                return MagicMock()
            # Filter out icacls (config file ACL hardening via
            if isinstance(cmd, list | tuple) and cmd and "icacls" in str(cmd[0]):
                return MagicMock()
            # Filter out lscpu (CPU inventory probe run by a library
            if isinstance(cmd, list | tuple) and cmd and "lscpu" in str(cmd[0]):
                return MagicMock()
            if isinstance(cmd, str) and "lscpu" in cmd:
                return MagicMock()
            popen_calls.append((a, kw))
            return MagicMock()

        monkeypatch.setattr("subprocess.Popen", _record_popen)
        startfile_calls: list = []
        monkeypatch.setattr("os.startfile", lambda p: startfile_calls.append(p), raising=False)

        app._open_config_file()

        config_file = app.config.config_dir / "config.json"
        assert open_calls == [str(config_file)], (
            f"XPLAT-01: default-app open must be used for the user's .json association. Got: {open_calls}"
        )
        assert popen_calls == [], (
            f"XPLAT-01: Notepad Popen must NOT be used on the association path. Got: {popen_calls}"
        )
        assert startfile_calls == [], "os.startfile must not be the primary path."

    def test_falls_back_to_systemroot_notepad_when_no_association(self, tmp_config_dir, monkeypatch):
        """No .json handler -> SystemRoot-validated Notepad, not bare notepad."""
        app = make_voice_typer_app(tmp_config_dir, monkeypatch)
        monkeypatch.setattr("voice_typer.server.platform_utils.is_windows", lambda: True)
        monkeypatch.setattr("voice_typer.server.platform_utils.is_macos", lambda: False)
        monkeypatch.setattr("voice_typer.server.platform_utils.is_linux", lambda: False)

        # Simulate "no associated handler": default-app open returns no handle.
        monkeypatch.setattr("voice_typer.server.app._windows_open_with_default_app", lambda path: None)
        # SystemRoot-validated Notepad path resolution.
        notepad_path = Path(r"C:\Windows\System32\notepad.exe")
        monkeypatch.setattr("voice_typer.server.app._systemroot_notepad_path", lambda: notepad_path)

        popen_calls: list = []

        class _FakeProc:
            def __init__(self, args):
                self._args = args

            def wait(self):
                return 0

        def _fake_popen(args, *rest, **kw):
            # Filter out library-init noise (e.g. `ldconfig -p` spawned by
            if isinstance(args, list | tuple) and args and "ldconfig" in str(args[0]):
                return _FakeProc(args)
            # Filter out icacls (config file ACL hardening via
            if isinstance(args, list | tuple) and args and "icacls" in str(args[0]):
                return _FakeProc(args)
            # Filter out lscpu (CPU inventory probe run by a library
            if isinstance(args, list | tuple) and args and "lscpu" in str(args[0]):
                return _FakeProc(args)
            if isinstance(args, str) and "lscpu" in args:
                return _FakeProc(args)
            popen_calls.append(args)
            return _FakeProc(args)

        monkeypatch.setattr("subprocess.Popen", _fake_popen)
        startfile_calls: list = []
        monkeypatch.setattr("os.startfile", lambda p: startfile_calls.append(p), raising=False)

        # SEC-audit-011 FALLBACK invariant (SystemRoot-validated
        monkeypatch.setattr(app.config, "save", lambda: True)

        app._open_config_file()

        config_file = app.config.config_dir / "config.json"
        assert len(popen_calls) == 1, (
            "SEC-audit-011: SystemRoot-validated Notepad must be used when no "
            f".json handler is associated. Got: {popen_calls}"
        )
        assert popen_calls[0] == [str(notepad_path), str(config_file)], (
            "SEC-audit-011: fallback must use the validated Notepad path, not a "
            f"bare PATH-resolved 'notepad'. Got: {popen_calls[0]!r}"
        )
        assert startfile_calls == [], "os.startfile must only be a last resort."

    def test_no_bare_path_resolved_notepad_in_source(self):
        """Source must not contain a bare PATH-resolved Popen(['notepad', ...])."""
        import ast

        app_py = Path(__file__).resolve().parent.parent / "voice_typer" / "server" / "app.py"
        src = app_py.read_text(encoding="utf-8")
        tree = ast.parse(src)

        violations: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr == "Popen"):
                continue
            if not node.args:
                continue
            first = node.args[0]
            if not isinstance(first, ast.List):
                continue
            for elt in first.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):  # noqa: SIM102
                    if elt.value == "notepad":
                        violations.append(f"line {node.lineno}: Popen([{elt.value!r}, ...])")

        assert not violations, (
            "XPLAT-01/SEC-audit-011: bare PATH-resolved Popen(['notepad', ...]) "
            f"found in app.py: {violations}. Use the SystemRoot-validated path."
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--no-cov"])
