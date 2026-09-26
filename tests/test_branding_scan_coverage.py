"""Coverage tests for ``scripts/check_branding.py``."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

# Resolve the script path relative to the repo root.
_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "check_branding.py"


def _run_check_branding(cwd: Path) -> subprocess.CompletedProcess:
    """Run ``scripts/check_branding.py`` with cwd set to ``cwd``."""
    return subprocess.run(
        [sys.executable, str(_SCRIPT_PATH)],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _violation_lines(result: subprocess.CompletedProcess) -> list[str]:
    """Extract the per-line violation entries from the script's stdout."""
    lines = result.stdout.splitlines()
    out: list[str] = []
    for line in lines:
        # Violation rows are indented and look like
        m = re.match(r"^\s+\S+:\d+:\s+(.*)$", line)
        if m:
            out.append(m.group(1))
    return out


def _make_fake_project_root(tmp_path: Path) -> Path:
    """
    Lay down the minimum files check_branding.py needs to boot.
    We do NOT create ``src-tauri/src/branding.rs``, the script's
    """
    root = tmp_path / "fake_root"
    (root / "voice_typer" / "server").mkdir(parents=True)
    (root / "voice_typer" / "server" / "branding.py").write_text('APP_NAME = "Lausu"\n', encoding="utf-8")
    return root


def _write_renderer_source(root: Path, rel_path: str, content: str) -> Path:
    """Write a fixture file under the fake root's renderer source tree."""
    target = root / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def test_tauri_conf_json_is_scanned(tmp_path):
    """A non-allowlisted literal in tauri.conf.json IS flagged."""
    root = _make_fake_project_root(tmp_path)
    (root / "src-tauri").mkdir(parents=True)
    (root / "src-tauri" / "tauri.conf.json").write_text(
        "{\n"
        '  "productName": "Lausu",\n'  # allowlisted
        '  "title": "Lausu",\n'  # allowlisted
        '  "description": "Lausu"\n'  # NOT allowlisted → flagged
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
    # productName / title lines must NOT appear in the violations list.
    assert len(violations) == 1, (
        f"expected exactly 1 violation (description only); got {len(violations)}:\n{violations}"
    )
    assert '"description": "Lausu"' in violations[0]
    for v in violations:
        assert "productName" not in v, f"productName should be allowlisted but was flagged: {v!r}"
        assert '"title":' not in v, f"title should be allowlisted but was flagged: {v!r}"


def test_deleted_builder_yml_is_not_a_scan_target(tmp_path):
    """
    Deleted legacy builder config is NOT scanned (previous host removed).
    Writing that path must not cause a branding violation: the scanner
    """
    root = _make_fake_project_root(tmp_path)
    (root / "voice_typer" / "client").mkdir(parents=True)
    (root / "voice_typer" / "client" / "legacy-builder.yml").write_text(
        'description: "Lausu"\n',
        encoding="utf-8",
    )
    result = _run_check_branding(root)
    assert result.returncode == 0, (
        f"deleted legacy builder config must not be scanned; "
        f"got rc={result.returncode}.\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )


def test_build_config_allowlist_only_passes(tmp_path):
    """A clean build-config file (only productName / title literals) passes."""
    root = _make_fake_project_root(tmp_path)
    (root / "src-tauri").mkdir(parents=True)
    (root / "src-tauri" / "tauri.conf.json").write_text(
        '{\n  "productName": "Lausu",\n  "title": "Lausu"\n}\n',
        encoding="utf-8",
    )
    result = _run_check_branding(root)
    assert result.returncode == 0, (
        f"expected exit 0 (clean build-config files); "
        f"got rc={result.returncode}.\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )


def test_real_project_branding_scan_passes():
    """Smoke test: running the scanner against the REAL project root exits 0."""
    repo_root = Path(__file__).resolve().parent.parent
    result = _run_check_branding(repo_root)
    assert result.returncode == 0, (
        f"real-project branding scan should exit 0; got rc={result.returncode}.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


# The tests below pin the substring pattern for ALL non-test client


def test_substring_in_literal_in_renderer_ts_source_is_flagged(tmp_path):
    """The brand inside a LONGER string literal in renderer .ts source"""
    root = _make_fake_project_root(tmp_path)
    _write_renderer_source(
        root,
        "voice_typer/client/src/renderer/src/bubble/helpers.ts",
        'export const label = tf("bubble.blockedIndicatorAria", "Lausu blocked indicator");\n',
    )
    result = _run_check_branding(root)
    assert result.returncode == 1, (
        f"expected exit 1 (substring literal must be flagged); "
        f"got rc={result.returncode}.\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    violations = _violation_lines(result)
    assert len(violations) == 1, f"expected exactly 1 violation; got {len(violations)}:\n{violations}"
    assert '"Lausu blocked indicator"' in violations[0]


def test_substring_in_literal_in_renderer_tsx_source_is_flagged(tmp_path):
    """Same coverage for .tsx files (JSX attributes, aria-labels)."""
    root = _make_fake_project_root(tmp_path)
    _write_renderer_source(
        root,
        "voice_typer/client/src/renderer/src/bubble/Bubble.tsx",
        'export function Bubble() {\n\treturn <output aria-label="Lausu paste failed indicator" />;\n}\n',
    )
    result = _run_check_branding(root)
    assert result.returncode == 1, f"expected exit 1; got rc={result.returncode}.\nstdout:\n{result.stdout}"
    violations = _violation_lines(result)
    assert any('aria-label="Lausu paste failed indicator"' in v for v in violations), (
        f"aria-label substring literal not flagged:\n{violations}"
    )


def test_substring_literal_on_comment_line_is_not_flagged(tmp_path):
    """Comment lines keep their exemption under the substring pattern."""
    root = _make_fake_project_root(tmp_path)
    _write_renderer_source(
        root,
        "voice_typer/client/src/renderer/src/bubble/helpers.ts",
        '// legacy: "Lausu blocked indicator" was removed\nexport const x = 1;\n',
    )
    result = _run_check_branding(root)
    assert result.returncode == 0, (
        f"comment-line literal must stay exempt; rc={result.returncode}.\nstdout:\n{result.stdout}"
    )


def test_substring_literal_in_renderer_locale_file_is_not_flagged(tmp_path):
    """Renderer locale files keep their exemption under the substring"""
    root = _make_fake_project_root(tmp_path)
    _write_renderer_source(
        root,
        "voice_typer/client/src/renderer/src/i18n/translations/en.json",
        '{\n\t"bubble": {\n\t\t"blockedIndicatorAria": "Lausu blocked indicator"\n\t}\n}\n',
    )
    result = _run_check_branding(root)
    assert result.returncode == 0, (
        f"renderer translations must stay exempt; rc={result.returncode}.\nstdout:\n{result.stdout}"
    )


def test_source_of_truth_branding_file_is_not_flagged(tmp_path):
    """The renderer source-of-truth branding.ts keeps its exemption (the"""
    root = _make_fake_project_root(tmp_path)
    _write_renderer_source(
        root,
        "voice_typer/client/src/renderer/src/branding.ts",
        'export const APP_NAME = "Lausu";\n',
    )
    result = _run_check_branding(root)
    assert result.returncode == 0, f"branding.ts must stay exempt; rc={result.returncode}.\nstdout:\n{result.stdout}"


def test_standalone_literal_in_renderer_source_flagged_exactly_once(tmp_path):
    """A standalone quoted literal is still flagged, and only once (the"""
    root = _make_fake_project_root(tmp_path)
    _write_renderer_source(
        root,
        "voice_typer/client/src/renderer/src/pages/Home.tsx",
        'const appName = "Lausu";\n',
    )
    result = _run_check_branding(root)
    assert result.returncode == 1
    violations = _violation_lines(result)
    assert len(violations) == 1, (
        f"expected exactly 1 violation (no double-report); got {len(violations)}:\n{violations}"
    )
    assert 'const appName = "Lausu";' in violations[0]


def test_app_name_composed_line_is_not_flagged(tmp_path):
    """A line that composes the string from the APP_NAME constant is the"""
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
    """Renderer test files are outside the substring pattern's scope."""
    root = _make_fake_project_root(tmp_path)
    _write_renderer_source(
        root,
        "voice_typer/client/src/renderer/src/bubble/__tests__/helpers.test.ts",
        'expect(label).toBe("Lausu blocked indicator");\n',
    )
    _write_renderer_source(
        root,
        "voice_typer/client/src/renderer/src/bubble/helpers.spec.tsx",
        'expect(label).toBe("Lausu blocked indicator");\n',
    )
    result = _run_check_branding(root)
    assert result.returncode == 0, (
        f"test files must stay out of substring scope; rc={result.returncode}.\nstdout:\n{result.stdout}"
    )


def test_main_process_ts_substring_literal_is_flagged(tmp_path):
    """Main-process .ts source is IN the substring pattern's scope."""
    root = _make_fake_project_root(tmp_path)
    _write_renderer_source(
        root,
        "voice_typer/client/src/main/single_instance.ts",
        "log.warn(`PID ${pid} is alive but is not Lausu`);\n",
    )
    result = _run_check_branding(root)
    assert result.returncode == 1, (
        f"main-process substring literal must be flagged (widened scope); "
        f"rc={result.returncode}.\nstdout:\n{result.stdout}"
    )
    violations = _violation_lines(result)
    assert len(violations) == 1, f"expected exactly 1 violation; got {len(violations)}:\n{violations}"
    assert "is not Lausu" in violations[0]


def test_substring_in_literal_in_preload_ts_source_is_flagged(tmp_path):
    """Preload .ts source is IN the substring pattern's scope (widened)."""
    root = _make_fake_project_root(tmp_path)
    _write_renderer_source(
        root,
        "voice_typer/client/src/preload/index.ts",
        'export const BRIDGE_NAME = "Lausu bridge";\n',
    )
    result = _run_check_branding(root)
    assert result.returncode == 1, (
        f"preload substring literal must be flagged (widened scope); rc={result.returncode}.\nstdout:\n{result.stdout}"
    )
    violations = _violation_lines(result)
    assert len(violations) == 1, f"expected exactly 1 violation; got {len(violations)}:\n{violations}"
    assert '"Lausu bridge"' in violations[0]


def test_substring_in_literal_in_shared_ts_source_is_flagged(tmp_path):
    """Shared .ts source is IN the substring pattern's scope (widened)."""
    root = _make_fake_project_root(tmp_path)
    _write_renderer_source(
        root,
        "voice_typer/client/src/shared/constants.ts",
        'export const CRASH_TITLE = "Lausu crashed";\n',
    )
    result = _run_check_branding(root)
    assert result.returncode == 1, (
        f"shared substring literal must be flagged (widened scope); rc={result.returncode}.\nstdout:\n{result.stdout}"
    )
    violations = _violation_lines(result)
    assert len(violations) == 1, f"expected exactly 1 violation; got {len(violations)}:\n{violations}"
    assert '"Lausu crashed"' in violations[0]


def test_main_process_app_name_composed_line_is_not_flagged(tmp_path):
    """The migrated single_instance.ts pattern (brand via ``${APP_NAME}``"""
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
    """Main-process test files stay OUT of the substring pattern's scope"""
    root = _make_fake_project_root(tmp_path)
    _write_renderer_source(
        root,
        "voice_typer/client/src/main/__tests__/single_instance.test.ts",
        'expect(msg).toBe("PID 42 is alive but is not Lausu");\n',
    )
    result = _run_check_branding(root)
    assert result.returncode == 0, (
        f"main-process test file must stay out of substring scope; rc={result.returncode}.\nstdout:\n{result.stdout}"
    )


def test_clean_renderer_source_with_placeholder_passes(tmp_path):
    """A renderer source file that routes the brand through the i18n"""
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
