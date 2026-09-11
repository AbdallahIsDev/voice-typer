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
  "build-config literal" exception to C-BRAND-1, they are NOT flagged.
* A clean build-config file (only productName / title literals) passes.

A second gap (found when the bubble aria fallbacks shipped with the
brand embedded inside LONGER string literals): the scanner's literal
pattern only matched the brand as a QUOTED STANDALONE literal, so a
source line like ``"Voice Typer blocked indicator"`` passed untouched.
The substring-in-literal tests below pin the scanner's second pattern
plus the exemptions it must keep intact (comments, renderer locale
files, the source-of-truth branding files, APP_NAME-composed lines).
The substring pattern's scope is ALL non-test .ts/.tsx under
``voice_typer/client/src/`` (main, preload, shared, renderer), the
renderer-only scope was widened after the last blocking legacy
literal (``src/main/single_instance.ts``) was migrated to
``${APP_NAME}``; the main/preload/shared tests below pin the widened
boundary, and the test-file exemption stays.
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
    more importantly, which were NOT, e.g. allowlisted productName
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

    * ``voice_typer/server/branding.py``, provides APP_NAME.
    * The two build-config files are added per-test (so each test
      controls their exact contents).

    We do NOT create ``src-tauri/src/branding.rs``, the script's
    cross-language parity check is skipped when that file is absent
    (``_read_rust_app_name`` returns None), so the fake root doesn't
    need a Rust mirror.
    """
    root = tmp_path / "fake_root"
    (root / "voice_typer" / "server").mkdir(parents=True)
    (root / "voice_typer" / "server" / "branding.py").write_text('APP_NAME = "Voice Typer"\n', encoding="utf-8")
    return root


def _write_renderer_source(root: Path, rel_path: str, content: str) -> Path:
    """Write a fixture file under the fake root's renderer source tree."""
    target = root / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


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
        # Match the YAML key prefix `title:`, careful not to match
        # the substring inside `description:` (which does NOT contain
        # `title:`, verified: "description" has no "title" substring).
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


# ── Substring-in-literal pattern (brand embedded inside LONGER string
# literals) ─────────────────────────────────────────────────────────────
#
# The historical gap: the literal pattern matched the brand only as a
# quoted STANDALONE literal (quote + brand + quote). A source line like
#     return tf("bubble.blockedIndicatorAria", "Voice Typer blocked indicator");
# embeds the brand inside a longer literal and passed the scanner
# untouched, exactly how the hardcoded bubble aria fallbacks shipped.
# The tests below pin the substring pattern for ALL non-test client
# .ts/.tsx source (main / preload / shared / renderer, the scope was
# widened from renderer-only once the last legacy main-process literal
# was migrated to ``${APP_NAME}``) and the exemptions it must keep.


def test_substring_in_literal_in_renderer_ts_source_is_flagged(tmp_path):
    """The brand inside a LONGER string literal in renderer .ts source
    is flagged (the bubble-aria class of violation)."""
    root = _make_fake_project_root(tmp_path)
    _write_renderer_source(
        root,
        "voice_typer/client/src/renderer/src/bubble/helpers.ts",
        'export const label = tf("bubble.blockedIndicatorAria", "Voice Typer blocked indicator");\n',
    )
    result = _run_check_branding(root)
    assert result.returncode == 1, (
        f"expected exit 1 (substring literal must be flagged); "
        f"got rc={result.returncode}.\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    violations = _violation_lines(result)
    assert len(violations) == 1, f"expected exactly 1 violation; got {len(violations)}:\n{violations}"
    assert '"Voice Typer blocked indicator"' in violations[0]


def test_substring_in_literal_in_renderer_tsx_source_is_flagged(tmp_path):
    """Same coverage for .tsx files (JSX attributes, aria-labels)."""
    root = _make_fake_project_root(tmp_path)
    _write_renderer_source(
        root,
        "voice_typer/client/src/renderer/src/bubble/Bubble.tsx",
        'export function Bubble() {\n\treturn <output aria-label="Voice Typer paste failed indicator" />;\n}\n',
    )
    result = _run_check_branding(root)
    assert result.returncode == 1, f"expected exit 1; got rc={result.returncode}.\nstdout:\n{result.stdout}"
    violations = _violation_lines(result)
    assert any('aria-label="Voice Typer paste failed indicator"' in v for v in violations), (
        f"aria-label substring literal not flagged:\n{violations}"
    )


def test_substring_literal_on_comment_line_is_not_flagged(tmp_path):
    """Comment lines keep their exemption under the substring pattern."""
    root = _make_fake_project_root(tmp_path)
    _write_renderer_source(
        root,
        "voice_typer/client/src/renderer/src/bubble/helpers.ts",
        '// legacy: "Voice Typer blocked indicator" was removed\nexport const x = 1;\n',
    )
    result = _run_check_branding(root)
    assert result.returncode == 0, (
        f"comment-line literal must stay exempt; rc={result.returncode}.\nstdout:\n{result.stdout}"
    )


def test_substring_literal_in_renderer_locale_file_is_not_flagged(tmp_path):
    """Renderer locale files keep their exemption under the substring
    pattern (they localize via the {appName} placeholder, but the whole
    translations directory is exempt)."""
    root = _make_fake_project_root(tmp_path)
    _write_renderer_source(
        root,
        "voice_typer/client/src/renderer/src/i18n/translations/en.json",
        '{\n\t"bubble": {\n\t\t"blockedIndicatorAria": "Voice Typer blocked indicator"\n\t}\n}\n',
    )
    result = _run_check_branding(root)
    assert result.returncode == 0, (
        f"renderer translations must stay exempt; rc={result.returncode}.\nstdout:\n{result.stdout}"
    )


def test_source_of_truth_branding_file_is_not_flagged(tmp_path):
    """The renderer source-of-truth branding.ts keeps its exemption (the
    one file allowed to define the literal)."""
    root = _make_fake_project_root(tmp_path)
    _write_renderer_source(
        root,
        "voice_typer/client/src/renderer/src/branding.ts",
        'export const APP_NAME = "Voice Typer";\n',
    )
    result = _run_check_branding(root)
    assert result.returncode == 0, f"branding.ts must stay exempt; rc={result.returncode}.\nstdout:\n{result.stdout}"


def test_standalone_literal_in_renderer_source_flagged_exactly_once(tmp_path):
    """A standalone quoted literal is still flagged, and only once (the
    substring pattern must not double-report the same line)."""
    root = _make_fake_project_root(tmp_path)
    _write_renderer_source(
        root,
        "voice_typer/client/src/renderer/src/pages/Home.tsx",
        'const appName = "Voice Typer";\n',
    )
    result = _run_check_branding(root)
    assert result.returncode == 1
    violations = _violation_lines(result)
    assert len(violations) == 1, (
        f"expected exactly 1 violation (no double-report); got {len(violations)}:\n{violations}"
    )
    assert 'const appName = "Voice Typer";' in violations[0]


def test_app_name_composed_line_is_not_flagged(tmp_path):
    """A line that composes the string from the APP_NAME constant is the
    CORRECT pattern and must never be flagged."""
    root = _make_fake_project_root(tmp_path)
    _write_renderer_source(
        root,
        "voice_typer/client/src/renderer/src/bubble/helpers.ts",
        'import { APP_NAME } from "@/branding";\nexport const label = `${APP_NAME} blocked indicator`;\n',
    )
    result = _run_check_branding(root)
    assert result.returncode == 0, (
        f"APP_NAME-composed line must not be flagged; rc={result.returncode}.\nstdout:\n{result.stdout}"
    )


def test_renderer_test_files_are_out_of_substring_scope(tmp_path):
    """Renderer test files are outside the substring pattern's scope.

    Test files assert golden OUTPUT values (e.g. the aria label after
    {appName} substitution) as literal expectations, pinning the
    literal in the test is the point of a golden assertion. This test
    documents the scope boundary; widening the pattern to tests would
    require migrating those golden assertions first.
    """
    root = _make_fake_project_root(tmp_path)
    _write_renderer_source(
        root,
        "voice_typer/client/src/renderer/src/bubble/__tests__/helpers.test.ts",
        'expect(label).toBe("Voice Typer blocked indicator");\n',
    )
    _write_renderer_source(
        root,
        "voice_typer/client/src/renderer/src/bubble/helpers.spec.tsx",
        'expect(label).toBe("Voice Typer blocked indicator");\n',
    )
    result = _run_check_branding(root)
    assert result.returncode == 0, (
        f"test files must stay out of substring scope; rc={result.returncode}.\nstdout:\n{result.stdout}"
    )


def test_main_process_ts_substring_literal_is_flagged(tmp_path):
    """Main-process .ts source is IN the substring pattern's scope.

    The scope was originally renderer-only because the repo carried a
    legacy log literal (``src/main/single_instance.ts``: "is not
    <brand>" inside a template literal). That literal was migrated to
    ``${APP_NAME}``, an audit confirmed zero other non-comment
    non-exempt brand literals in main/preload/shared non-test TS, and
    the pattern was widened to the full client source tree, so a
    fixture main-process violation now FAILS the checker.
    """
    root = _make_fake_project_root(tmp_path)
    _write_renderer_source(
        root,
        "voice_typer/client/src/main/single_instance.ts",
        "log.warn(`PID ${pid} is alive but is not Voice Typer`);\n",
    )
    result = _run_check_branding(root)
    assert result.returncode == 1, (
        f"main-process substring literal must be flagged (widened scope); "
        f"rc={result.returncode}.\nstdout:\n{result.stdout}"
    )
    violations = _violation_lines(result)
    assert len(violations) == 1, f"expected exactly 1 violation; got {len(violations)}:\n{violations}"
    assert "is not Voice Typer" in violations[0]


def test_substring_in_literal_in_preload_ts_source_is_flagged(tmp_path):
    """Preload .ts source is IN the substring pattern's scope (widened)."""
    root = _make_fake_project_root(tmp_path)
    _write_renderer_source(
        root,
        "voice_typer/client/src/preload/index.ts",
        'export const BRIDGE_NAME = "Voice Typer bridge";\n',
    )
    result = _run_check_branding(root)
    assert result.returncode == 1, (
        f"preload substring literal must be flagged (widened scope); rc={result.returncode}.\nstdout:\n{result.stdout}"
    )
    violations = _violation_lines(result)
    assert len(violations) == 1, f"expected exactly 1 violation; got {len(violations)}:\n{violations}"
    assert '"Voice Typer bridge"' in violations[0]


def test_substring_in_literal_in_shared_ts_source_is_flagged(tmp_path):
    """Shared .ts source is IN the substring pattern's scope (widened)."""
    root = _make_fake_project_root(tmp_path)
    _write_renderer_source(
        root,
        "voice_typer/client/src/shared/constants.ts",
        'export const CRASH_TITLE = "Voice Typer crashed";\n',
    )
    result = _run_check_branding(root)
    assert result.returncode == 1, (
        f"shared substring literal must be flagged (widened scope); rc={result.returncode}.\nstdout:\n{result.stdout}"
    )
    violations = _violation_lines(result)
    assert len(violations) == 1, f"expected exactly 1 violation; got {len(violations)}:\n{violations}"
    assert '"Voice Typer crashed"' in violations[0]


def test_main_process_app_name_composed_line_is_not_flagged(tmp_path):
    """The migrated single_instance.ts pattern (brand via ``${APP_NAME}``
    in a template literal) is the CORRECT pattern and must not be
    flagged, pins that the real main-process file passes the widened
    scan."""
    root = _make_fake_project_root(tmp_path)
    _write_renderer_source(
        root,
        "voice_typer/client/src/main/single_instance.ts",
        'import { APP_NAME } from "./branding";\nlog.warn(`PID ${pid} is alive but is not ${APP_NAME} `);\n',
    )
    result = _run_check_branding(root)
    assert result.returncode == 0, (
        f"APP_NAME-composed main-process line must not be flagged; rc={result.returncode}.\nstdout:\n{result.stdout}"
    )


def test_main_process_test_files_are_out_of_substring_scope(tmp_path):
    """Main-process test files stay OUT of the substring pattern's scope
    even under the widened client-tree prefix (golden-output pins)."""
    root = _make_fake_project_root(tmp_path)
    _write_renderer_source(
        root,
        "voice_typer/client/src/main/__tests__/single_instance.test.ts",
        'expect(msg).toBe("PID 42 is alive but is not Voice Typer");\n',
    )
    result = _run_check_branding(root)
    assert result.returncode == 0, (
        f"main-process test file must stay out of substring scope; rc={result.returncode}.\nstdout:\n{result.stdout}"
    )


def test_clean_renderer_source_with_placeholder_passes(tmp_path):
    """A renderer source file that routes the brand through the i18n
    {appName} placeholder (no literal brand at all) passes."""
    root = _make_fake_project_root(tmp_path)
    _write_renderer_source(
        root,
        "voice_typer/client/src/renderer/src/bubble/helpers.ts",
        'import { t } from "@/i18n/i18n";\nexport const label = t("bubble.blockedIndicatorAria");\n',
    )
    result = _run_check_branding(root)
    assert result.returncode == 0, f"clean renderer source must pass; rc={result.returncode}.\nstdout:\n{result.stdout}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "--no-cov"]))
