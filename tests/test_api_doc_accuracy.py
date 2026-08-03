"""H1 + H2 tests: API.md config-table accuracy and Windows notepad behavior.

H1 (d-review Finding 3)
-----------------------
``docs/API.md``'s "Key Configuration Keys" table previously documented
5+ stale or fabricated field definitions (wrong defaults, removed
fields, invented enum values).  This test parses the markdown table
and asserts each row matches the actual ``Config`` dataclass default
read from ``voice_typer/server/config.py``.

If you change a default in ``Config``, update the table in
``docs/API.md`` in the same commit — otherwise this test fails and CI
blocks the PR.

H2 (c-review XPLAT-01)
-----------------------
On Windows, ``VoiceTyperApp._open_config_file`` opens the user's
``.json`` file association (e.g. VS Code, Notepad++, Sublime) via
``ShellExecuteEx`` so it can still block until the editor exits and
reload the config afterward — unlike ``os.startfile`` which returns
immediately with no process handle.  When no ``.json`` handler is
associated it falls back to the SystemRoot-validated Notepad path
(never a bare PATH-resolved ``notepad``).  This preserves the
XPLAT-01 UX win (respecting associations) while restoring the
pre-XPLAT-01 SEC-audit-011 guarantees: the ``_config_mutation_lock`` is
held for the editor session and the config is reloaded after the
editor closes.

These tests mock the ShellExecuteEx wrapper and ``subprocess.Popen`` to
verify both branches without spawning real editors.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tests.fixtures.app_helpers import make_voice_typer_app

# the previous Linux test-env shim that aliased
# ``ctypes.WINFUNCTYPE = ctypes.CFUNCTYPE`` and inserted a ``MagicMock``
# for ``voice_typer.server.crash_handler`` into ``sys.modules`` has been
# removed. ``crash_handler.py`` now gates the ``@ctypes.WINFUNCTYPE(...)``
# decorator behind ``sys.platform == "win32"``, so the module imports
# cleanly on Linux/macOS without any test-infrastructure shim.
# ─── H1: API.md config-table accuracy ─────────────────────────────────


def _api_md_path() -> Path:
    """Return the absolute path to ``docs/API.md``.

    Resolved relative to this test file so the test works regardless of
    the pytest ``rootdir`` (e.g. when run via ``pytest tests/`` from the
    repo root vs ``pytest`` from a subdirectory).
    """
    return Path(__file__).resolve().parent.parent / "docs" / "API.md"


def _parse_api_config_table(api_md_text: str) -> list[tuple[str, str, str, str]]:
    """Parse the "Key Configuration Keys" markdown table from API.md.

    Returns a list of ``(key, type_str, default_str, description)``
    tuples — one per data row.  The header row and the separator row
    (``|-----|------|---------|-------------|``) are skipped.

    The parser is intentionally simple (regex + ``split("|")``) so it
    has no third-party deps.  It does NOT try to handle GitHub-flavored
    markdown extensions — only the pipe-table syntax used in API.md.
    """
    # Anchor on the heading + the table header row so we don't
    # accidentally pick up unrelated tables elsewhere in the file.
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
        "docs/API.md — did the heading or table header change?"
    )

    rows: list[tuple[str, str, str, str]] = []
    for line in match.group("rows").splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        # ``strip("|")`` removes leading/trailing pipes; ``split("|")``
        # then gives us the 4 cells.  We re-strip each cell to remove
        # the surrounding spaces.
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) != 4:
            continue
        key, type_str, default_str, description = cells
        # Skip the separator row (``-----|------|---------|-------------``)
        # in case it slipped through — its "key" cell is all dashes.
        if not key or key.startswith("-"):
            continue

        # API.md wraps identifiers and literals in backticks for monospace
        # rendering.  Strip a single layer of surrounding backticks so
        # ``key`` is ``recording_mode`` (not `` `recording_mode` ``),
        # ``type_str`` is ``str`` (not `` `str` ``), and ``default_str``
        # is ``"toggle"`` (not `` `"toggle"` ``).  Only the outermost
        # backtick layer is stripped — inline backticks inside the
        # description (e.g. ``One of: `toggle`, `push_to_talk`.``) are
        # preserved.
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
    """Coerce a documented default string to the corresponding Python value.

    The API.md table renders defaults as Python-literal-ish strings:
    ``"toggle"``, ``True``, ``20.0``, ``150``, etc.  This helper parses
    them back to their native types so we can ``==`` compare against the
    actual ``Config()`` default.
    """
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
        # strings in the table — every str default in API.md uses
        # double quotes — so a single-quoted value is a doc bug worth
        # surfacing as a test failure.
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
        """Every (key, default) in the table must match ``Config()``.

        This is the core regression guard for d-review Finding 3.  If
        you change a default in ``Config``, update API.md in the same
        commit.  If you remove a field from ``Config``, remove it from
        the table.  If you add a user-facing field, consider adding it
        to the table too (and this test will catch typos in the default
        value).
        """
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
        """Removed/renamed fields must NOT reappear in the table.

        d-review Finding 3 specifically called out ``paste_enabled``,
        ``clipboard_clear_delay_seconds``, ``check_updates``, and the
        ``voice_activity`` recording mode as stale entries.  This test
        pins that they stay removed.
        """
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
            f"These were called out as stale in d-review Finding 3 — do NOT re-add."
        )

    def test_recording_mode_enum_matches_validator(self):
        """The recording_mode description must list the real enum values.

        The validator in ``config_validators.py`` is
        ``_make_enum_validator({"toggle", "push_to_talk"})`` — there is
        no ``voice_activity`` mode.  The description in API.md must not
        advertise ``voice_activity`` as a valid value.
        """
        rows = _parse_api_config_table(_api_md_path().read_text(encoding="utf-8"))
        recording_mode_row = next((r for r in rows if r[0] == "recording_mode"), None)
        assert recording_mode_row is not None, "recording_mode row missing from table"
        _key, _type, _default, desc = recording_mode_row
        assert "voice_activity" not in desc, (
            "API.md still lists 'voice_activity' as a valid recording_mode — "
            "this value was never implemented. The enum is {toggle, push_to_talk} only."
        )
        # Both real enum values must be advertised.
        assert "toggle" in desc and "push_to_talk" in desc, (
            "API.md recording_mode description must list both 'toggle' and "
            f"'push_to_talk' (the actual enum). Got: {desc!r}"
        )


# ─── H2: Windows _open_config_file — default-app open, validated notepad fallback ─


class TestWindowsOpenConfigFile:
    """XPLAT-01 + SEC-audit-011: Windows _open_config_file opens the user's
    default editor (respecting .json associations) but still holds
    _config_mutation_lock for the session and reloads config after the
    editor closes. It does this via ShellExecuteEx (which yields a process
    handle to wait on) rather than os.startfile (which returns immediately
    with no handle). When no .json handler is associated it falls back to
    the SystemRoot-validated Notepad path, never a bare PATH-resolved
    "notepad".
    """

    def test_opens_with_default_app_first_when_associated(self, tmp_config_dir, monkeypatch):
        """Primary path uses the default-app open (association-respecting)."""
        app = make_voice_typer_app(tmp_config_dir, monkeypatch)
        monkeypatch.setattr("voice_typer.server.app.is_windows", lambda: True)
        monkeypatch.setattr("voice_typer.server.app.is_macos", lambda: False)
        monkeypatch.setattr("voice_typer.server.app.is_linux", lambda: False)

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
            # ctypes.CDLL / dynamic-linker probing during import). These calls
            # are Python stdlib internals, not SUT behavior, and would otherwise
            # leak into the recorder and break assertions that expect ZERO
            # Notepad-related Popen calls on the default-app path.
            cmd = a[0] if a else kw.get("args")
            if isinstance(cmd, list | tuple) and cmd and "ldconfig" in str(cmd[0]):
                return MagicMock()
            # Filter out icacls (config file ACL hardening via
            # config/__init__.py _restrict_config_file_acl) — it's a
            # security step that runs during config save, not an editor
            # invocation. Without this filter the assertion below
            # (popen_calls == []) fails because icacls leaks into the
            # recorder even though no Notepad/editor Popen was issued.
            if isinstance(cmd, list | tuple) and cmd and "icacls" in str(cmd[0]):
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
        monkeypatch.setattr("voice_typer.server.app.is_windows", lambda: True)
        monkeypatch.setattr("voice_typer.server.app.is_macos", lambda: False)
        monkeypatch.setattr("voice_typer.server.app.is_linux", lambda: False)

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
            # ctypes.CDLL / dynamic-linker probing during import). These calls
            # are Python stdlib internals, not SUT behavior; the only Popen
            # the SUT should issue here is the SystemRoot-validated Notepad
            # fallback (['C:\\Windows\\System32\\notepad.exe', config_file]).
            if isinstance(args, list | tuple) and args and "ldconfig" in str(args[0]):
                return _FakeProc(args)
            # Filter out icacls (config file ACL hardening via
            # config/__init__.py _restrict_config_file_acl) — it's a
            # security step that runs during config save, not an editor
            # invocation. Without this filter the assertion below
            # (len(popen_calls) == 1) fails because icacls leaks into
            # the recorder alongside the Notepad fallback.
            if isinstance(args, list | tuple) and args and "icacls" in str(args[0]):
                return _FakeProc(args)
            popen_calls.append(args)
            return _FakeProc(args)

        monkeypatch.setattr("subprocess.Popen", _fake_popen)
        startfile_calls: list = []
        monkeypatch.setattr("os.startfile", lambda p: startfile_calls.append(p), raising=False)

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
        """Source must not contain a bare PATH-resolved Popen(['notepad', ...]).

        The insecure downgrade pattern (``subprocess.Popen(['notepad', ...])``
        resolved via PATH/cwd) must be gone. The validated fallback builds the
        Notepad path from %SYSTEMROOT% and passes it as a concrete file path.
        """
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
