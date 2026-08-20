"""Coverage tests for ``scripts/check_branding.py``.

The original scanner only walked ``voice_typer/server``,
``voice_typer/client/src``, ``src-tauri/src``, and two top-level
Python files. It missed the two build-config files that legitimately
need literal "Voice Typer" strings in their productName/title fields
(``src-tauri/tauri.conf.json`` and ``voice_typer/client/electron-builder.yml``).
These tests pin the new behavior:

* Both build-config files ARE scanned (a non-allowlisted literal in
  them is flagged).
* The productName / title fields are allowlisted as a documented
  "build-config literal" exception to C-BRAND-1 — they are NOT flagged.
* A clean build-config file (only productName / title literals) passes.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

# Resolve the script path relative to the repo root.
_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "check_branding.py"


def _run_check_branding(cwd: Path) -> subprocess.CompletedProcess:
    """Run ``scripts/check_branding.py`` with cwd set to ``cwd``.

    Running in a fresh cwd lets each test fixture own a fake project
    root (with its own branding.py, src-tauri/tauri.conf.json, etc.)
    without polluting the real repo or other tests. The script reads
    ``Path("voice_typer/server/branding.py")`` relative to cwd at
    module-import time, so cwd must contain that file.
    """
    return subprocess.run(
        [sys.executable, str(_SCRIPT_PATH)],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _violation_lines(result: subprocess.CompletedProcess) -> list[str]:
    """Extract the per-line violation entries from the script's stdout.

    The script prints::

        ERROR: Found N hardcoded references to 'Voice Typer' ...

          <abs_path>:<lineno>:  <stripped_line>

    We pull out the ``<stripped_line>`` portion of each violation so
    tests can assert exactly which source lines were flagged (and,
    more importantly, which were NOT — e.g. allowlisted productName
    / title lines must never appear in this list).
    """
    lines = result.stdout.splitlines()
    out: list[str] = []
    for line in lines:
        # Violation rows are indented and look like
        # ``  /path/to/file:42:  <source line>``. Match anything that
        # has the ``:LINENO:`` separator followed by the source text.
        m = re.match(r"^\s+\S+:\d+:\s+(.*)$", line)
        if m:
            out.append(m.group(1))
    return out


def _make_fake_project_root(tmp_path: Path) -> Path:
    """Lay down the minimum files check_branding.py needs to boot.

    * ``voice_typer/server/branding.py`` — provides APP_NAME.
    * The two build-config files are added per-test (so each test
      controls their exact contents).

    We do NOT create ``src-tauri/src/branding.rs`` — the script's
    cross-language parity check is skipped when that file is absent
    (``_read_rust_app_name`` returns None), so the fake root doesn't
    need a Rust mirror.
    """
    root = tmp_path / "fake_root"
    (root / "voice_typer" / "server").mkdir(parents=True)
    (root / "voice_typer" / "server" / "branding.py").write_text('APP_NAME = "Voice Typer"\n', encoding="utf-8")
    return root


def test_tauri_conf_json_is_scanned(tmp_path):
    """A non-allowlisted literal in tauri.conf.json IS flagged.

    The ``description`` field is NOT in the productName/title allowlist,
    so a literal ``"Voice Typer"`` value there must be flagged. This
    proves the file is actually being scanned (the original scanner
    never looked at src-tauri/tauri.conf.json).
    """
    root = _make_fake_project_root(tmp_path)
    (root / "src-tauri").mkdir(parents=True)
    (root / "src-tauri" / "tauri.conf.json").write_text(
        "{\n"
        '  "productName": "Voice Typer",\n'  # allowlisted
        '  "title": "Voice Typer",\n'  # allowlisted
        '  "description": "Voice Typer"\n'  # NOT allowlisted → flagged
        "}\n",
        encoding="utf-8",
    )
    result = _run_check_branding(root)
    assert result.returncode == 1, (
        f"expected exit 1 (description literal must be flagged); "
        f"got rc={result.returncode}.\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    violations = _violation_lines(result)
    # Exactly ONE violation: the description line. The allowlisted
    # productName / title lines must NOT appear in the violations list.
    assert len(violations) == 1, (
        f"expected exactly 1 violation (description only); got {len(violations)}:\n{violations}"
    )
    assert '"description": "Voice Typer"' in violations[0]
    for v in violations:
        assert "productName" not in v, f"productName should be allowlisted but was flagged: {v!r}"
        assert '"title":' not in v, f"title should be allowlisted but was flagged: {v!r}"


def test_electron_builder_yml_is_scanned(tmp_path):
    """A non-allowlisted literal in electron-builder.yml IS flagged.

    The YAML ``description`` field is NOT in the productName/title
    allowlist, so a quoted literal ``"Voice Typer"`` value there must
    be flagged. This proves the file is actually being scanned (the
    original scanner never looked at voice_typer/client/electron-builder.yml
    AND did not include .yml/.yaml in EXTENSIONS).
    """
    root = _make_fake_project_root(tmp_path)
    (root / "voice_typer" / "client").mkdir(parents=True)
    (root / "voice_typer" / "client" / "electron-builder.yml").write_text(
        "productName: Voice Typer\n"  # allowlisted (also unquoted → wouldn't match regex anyway)
        'title: "Voice Typer"\n'  # allowlisted
        'description: "Voice Typer"\n'  # NOT allowlisted → flagged
        '# comment: "Voice Typer"\n',  # YAML comment → skipped
        encoding="utf-8",
    )
    result = _run_check_branding(root)
    assert result.returncode == 1, (
        f"expected exit 1 (description literal must be flagged); "
        f"got rc={result.returncode}.\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    violations = _violation_lines(result)
    assert len(violations) == 1, (
        f"expected exactly 1 violation (description only); got {len(violations)}:\n{violations}"
    )
    assert 'description: "Voice Typer"' in violations[0]
    # The allowlisted productName / title lines must NOT appear in
    # the violations list. We check for the YAML-key prefixes that
    # would be present if those lines were flagged.
    for v in violations:
        assert "productName:" not in v, f"productName should be allowlisted but was flagged: {v!r}"
        # Match the YAML key prefix `title:` — careful not to match
        # the substring inside `description:` (which does NOT contain
        # `title:` — verified: "description" has no "title" substring).
        assert not v.startswith("title:"), f"title should be allowlisted but was flagged: {v!r}"


def test_build_config_allowlist_only_passes(tmp_path):
    """A clean build-config file (only productName / title literals) passes.

    Both tauri.conf.json and electron-builder.yml, when they contain
    ONLY the allowlisted productName / title fields, must exit 0.
    This proves the allowlist actually exempts those fields (not just
    that the scanner skipped the files entirely).
    """
    root = _make_fake_project_root(tmp_path)
    (root / "src-tauri").mkdir(parents=True)
    (root / "src-tauri" / "tauri.conf.json").write_text(
        '{\n  "productName": "Voice Typer",\n  "title": "Voice Typer"\n}\n',
        encoding="utf-8",
    )
    (root / "voice_typer" / "client").mkdir(parents=True)
    (root / "voice_typer" / "client" / "electron-builder.yml").write_text(
        'productName: Voice Typer\ntitle: "Voice Typer"\n',
        encoding="utf-8",
    )
    result = _run_check_branding(root)
    assert result.returncode == 0, (
        f"expected exit 0 (clean build-config files); "
        f"got rc={result.returncode}.\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )


def test_real_project_branding_scan_passes():
    """Smoke test: running the scanner against the REAL project root exits 0.

    Guards against an allowlist that is too narrow (e.g. forgets the
    YAML form) and would flag the real electron-builder.yml /
    tauri.conf.json in CI.
    """
    repo_root = Path(__file__).resolve().parent.parent
    result = _run_check_branding(repo_root)
    assert result.returncode == 0, (
        f"real-project branding scan should exit 0; got rc={result.returncode}.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "--no-cov"]))
