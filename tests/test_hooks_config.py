"""Focused config-validation tests for the WAVE3-A03 hook-architecture fix."""

from __future__ import annotations

import json
import re
import textwrap
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]

PRE_COMMIT_CONFIG = PROJECT_ROOT / ".pre-commit-config.yaml"
HUSKY_PRE_COMMIT = PROJECT_ROOT / ".husky" / "pre-commit"
HUSKY_PRE_PUSH = PROJECT_ROOT / ".husky" / "pre-push"
CONTRIBUTING = PROJECT_ROOT / "CONTRIBUTING.md"
PACKAGE_JSON = PROJECT_ROOT / "voice_typer" / "client" / "package.json"
TSCONFIG_ROOT = PROJECT_ROOT / "voice_typer" / "client" / "tsconfig.json"


def test_pre_commit_config_has_no_mirrors_mypy_repo() -> None:
    """venv and reinstalled torch on every ``pre-commit run mypy``. The mypy"""
    cfg = yaml.safe_load(PRE_COMMIT_CONFIG.read_text())
    repos = [r["repo"] for r in cfg["repos"]]
    assert "https://github.com/pre-commit/mirrors-mypy" not in repos, (
        "mirrors-mypy repo should be removed; mypy is now a local hook "
        "with language: system to reuse the project venv (XS-35)."
    )


def test_pre_commit_config_header_documents_husky_sole_installer() -> None:
    """The header comment must explain that husky is the SOLE installer"""
    text = PRE_COMMIT_CONFIG.read_text()
    # Pull just the header comment block (everything before ``repos:``).
    header = text.split("repos:", 1)[0]
    assert "XS-34" in header, "header should reference XS-34 architecture"
    assert "husky" in header.lower()
    assert "pre-commit install" in header.lower()
    assert "core.hooksPath" in header or "hooksPath" in header


def test_husky_pre_commit_invokes_pre_commit_run() -> None:
    """Husky's ``.husky/pre-commit`` must invoke ``pre-commit run`` so"""
    text = HUSKY_PRE_COMMIT.read_text()
    assert "pre-commit run" in text, (
        ".husky/pre-commit must invoke `pre-commit run` to delegate to "
        "the pre-commit framework without `pre-commit install`."
    )
    assert "command -v pre-commit" in text, (
        ".husky/pre-commit must guard the `pre-commit run` block with "
        "`command -v pre-commit` so contributors who haven't installed "
        "the framework are silently skipped (no hard failure)."
    )


def test_husky_pre_commit_documents_sole_installer() -> None:
    """The ``.husky/pre-commit`` header must reference the XS-34 fix and"""
    text = HUSKY_PRE_COMMIT.read_text()
    # Header is the top comment block.
    header = text.split("echo", 1)[0] if "echo" in text else text[:2000]
    assert "XS-34" in header or "sole installer" in header.lower(), (
        ".husky/pre-commit header should reference XS-34 / sole-installer "
        "architecture so future maintainers understand the design."
    )


def test_contributing_tldr_does_not_recommend_pre_commit_install() -> None:
    """The TL;DR must NOT tell contributors to run ``pre-commit install``"""
    # CONTRIBUTING.md is UTF-8; the default locale encoding on Windows
    text = CONTRIBUTING.read_text(encoding="utf-8")
    # The TL;DR is the first blockquote. Use MULTILINE so ^ matches at
    tldr_match = re.search(
        r"^>\s\*\*TL;DR\*\*.*?(?=\n\n)",
        text,
        re.DOTALL | re.MULTILINE,
    )
    assert tldr_match, "TL;DR blockquote not found in CONTRIBUTING.md"
    tldr = tldr_match.group(0)
    # The TL;DR may mention `pre-commit install` only in a "Do NOT run"
    # parenthetical "Do NOT run" warning.
    bare_recommendation = re.search(r"`pre-commit install`,\s*then", tldr)
    assert bare_recommendation is None, (
        "TL;DR should not list `pre-commit install` as a recommended "
        "install step (XS-34). Found TL;DR:\n" + textwrap.indent(tldr, "    ")
    )


def test_contribing_warns_against_pre_commit_install() -> None:
    """CONTRIBUTING.md must contain an explicit \"Do NOT run"""
    # CONTRIBUTING.md is UTF-8; the default locale encoding on Windows
    text = CONTRIBUTING.read_text(encoding="utf-8")
    assert "Do NOT run `pre-commit install`" in text or ("Do NOT run\n`pre-commit install`" in text), (
        "CONTRIBUTING.md must contain an explicit 'Do NOT run `pre-commit install`' warning (XS-34)."
    )


def test_contributing_documents_mypy_local_hook() -> None:
    """``language: system`` so contributors know to activate the project"""
    # CONTRIBUTING.md is UTF-8; the default locale encoding on Windows
    text = CONTRIBUTING.read_text(encoding="utf-8")
    assert "language: system" in text, (
        "CONTRIBUTING.md should mention that mypy uses `language: system` to reuse the project venv (XS-35)."
    )
    assert "ML dep set" in text, "CONTRIBUTING.md should document the XS-35 rationale (no ML-dep reinstall)."


def _find_hook(cfg: dict, hook_id: str) -> dict:
    for repo in cfg["repos"]:
        for hook in repo.get("hooks", []):
            if hook["id"] == hook_id:
                return hook
    raise KeyError(f"hook {hook_id!r} not found in .pre-commit-config.yaml")


def test_mypy_hook_is_local_with_language_system() -> None:
    """The mypy hook must be a ``local`` hook with ``language: system``"""
    cfg = yaml.safe_load(PRE_COMMIT_CONFIG.read_text())
    mypy_hook = _find_hook(cfg, "mypy")
    assert mypy_hook["language"] == "system", (
        "mypy hook must use `language: system` to reuse the project "
        "venv (XS-35). Found language: " + repr(mypy_hook.get("language"))
    )
    assert "additional_dependencies" not in mypy_hook, (
        "mypy hook must NOT declare `additional_dependencies` (the "
        "torch reinstall was the XS-35 root cause). Found: " + repr(mypy_hook.get("additional_dependencies"))
    )


def test_mypy_hook_entry_uses_python_ratchet_script() -> None:
    """The mypy entry must be ``python scripts/mypy_ratchet_check.py``"""
    cfg = yaml.safe_load(PRE_COMMIT_CONFIG.read_text())
    mypy_hook = _find_hook(cfg, "mypy")
    assert mypy_hook["entry"] == "python scripts/mypy_ratchet_check.py", (
        "mypy hook entry must run `python scripts/mypy_ratchet_check.py` "
        "for cross-platform venv portability + the count-based ratchet "
        "(XS-35). Found: " + repr(mypy_hook.get("entry"))
    )


def test_mypy_hook_is_at_pre_push_stage() -> None:
    """mypy must stay at ``stages: [pre-push]`` so it does NOT run on"""
    cfg = yaml.safe_load(PRE_COMMIT_CONFIG.read_text())
    mypy_hook = _find_hook(cfg, "mypy")
    assert mypy_hook.get("stages") == ["pre-push"], "mypy hook must be at `stages: [pre-push]` (XS-35). Found: " + repr(
        mypy_hook.get("stages")
    )


def test_mypy_hook_files_scoped_to_server() -> None:
    """mypy must scope ``files: ^voice_typer/server/`` so it doesn't"""
    cfg = yaml.safe_load(PRE_COMMIT_CONFIG.read_text())
    mypy_hook = _find_hook(cfg, "mypy")
    assert mypy_hook.get("files") == "^voice_typer/server/", (
        "mypy hook must scope `files: ^voice_typer/server/`. Found: " + repr(mypy_hook.get("files"))
    )


def test_husky_pre_push_uses_cached_typecheck() -> None:
    """``.husky/pre-push`` must use ``npm run typecheck`` (cached,"""
    text = HUSKY_PRE_PUSH.read_text()
    code_lines = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    code_only = "\n".join(code_lines)
    assert "npm run typecheck" in code_only, (
        ".husky/pre-push must invoke `npm run typecheck` in its actual "
        "command (XS-35). Code-only content:\n" + textwrap.indent(code_only, "    ")
    )
    # `typecheck:ci` must NOT appear as the actual invocation. Use a
    ci_invocation = re.search(r"npm run typecheck:ci\b", code_only)
    assert ci_invocation is None, (
        ".husky/pre-push must NOT invoke `npm run typecheck:ci` as the "
        "actual pre-push typecheck (XS-35), it's cache-busting and was "
        "the original slowness root cause. Code-only content:\n" + textwrap.indent(code_only, "    ")
    )


def test_husky_pre_push_drops_pytest_keeps_fast_gates() -> None:
    """``.husky/pre-push`` must be LEAN: no pytest invocation at all, only"""
    text = HUSKY_PRE_PUSH.read_text()
    # Strip comment lines to isolate actual shell commands.
    code_lines = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    code_only = "\n".join(code_lines)
    assert "-m pytest" not in code_only, (
        ".husky/pre-push must NOT invoke pytest, the full suite is "
        "already greened at the end of every task; the push gate keeps "
        "only seconds-cost checks. Code-only content:\n" + textwrap.indent(code_only, "    ")
    )
    assert "pytest" not in code_only, (
        ".husky/pre-push must not run pytest in any form. Code-only content:\n" + textwrap.indent(code_only, "    ")
    )
    assert "npm run typecheck" in code_only, (
        ".husky/pre-push must keep the cached client typecheck. Code-only content:\n"
        + textwrap.indent(code_only, "    ")
    )
    assert "pre-commit run --hook-stage pre-push" in code_only, (
        ".husky/pre-push must keep the mypy ratchet (pre-commit, pre-push "
        "stage), mypy has no CI gate. Code-only content:\n" + textwrap.indent(code_only, "    ")
    )


def _load_package_json() -> dict:
    return json.loads(PACKAGE_JSON.read_text())


def test_typecheck_root_uses_build_mode() -> None:
    """``typecheck:root`` must use ``tsc -b --noEmit`` (build mode,"""
    pkg = _load_package_json()
    typecheck_root = pkg["scripts"]["typecheck:root"]
    assert typecheck_root.startswith("tsc -b"), (
        "`typecheck:root` must use `tsc -b` (build mode) to type-check "
        "the referenced projects in the solution-style tsconfig.json "
        "(XS-68). Found: " + repr(typecheck_root)
    )
    assert "--noEmit" in typecheck_root, (
        "`typecheck:root` must pass `--noEmit` to skip emitting build artifacts (XS-68). Found: " + repr(typecheck_root)
    )


def test_typecheck_does_not_start_with_no_op_tsc() -> None:
    """The ``typecheck`` script must NOT start with a bare ``tsc"""
    pkg = _load_package_json()
    typecheck = pkg["scripts"]["typecheck"]
    # The bare `tsc --noEmit` no-op is what we're guarding against.
    bare_no_op = typecheck.startswith("tsc --noEmit")
    assert not bare_no_op, (
        "`typecheck` script must NOT start with `tsc --noEmit`, it's "
        "a silent no-op against the solution-style tsconfig.json (XS-68). "
        "Found: " + repr(typecheck)
    )
    # And the real per-project checks must be present.
    assert "tsconfig.web.json" in typecheck, "`typecheck` must include the web project typecheck (XS-68)."
    assert "tsconfig.node.json" in typecheck, "`typecheck` must include the node project typecheck (XS-68)."


def test_tsconfig_root_is_solution_style() -> None:
    """Guard that ``tsconfig.json`` is still a solution-style config"""
    cfg = json.loads(TSCONFIG_ROOT.read_text())
    assert cfg.get("files") == [], (
        "tsconfig.json must be solution-style (files: []) for the "
        "XS-68 fix to remain meaningful. Found: " + repr(cfg.get("files"))
    )
    assert "references" in cfg and len(cfg["references"]) >= 2, (
        "tsconfig.json must reference at least 2 sub-projects (web + node) for the XS-68 fix to remain meaningful."
    )


if __name__ == "__main__":
    # Allow `python tests/test_hooks_config.py` for quick local runs.
    pytest.main([__file__, "-v", "--timeout=30"])
