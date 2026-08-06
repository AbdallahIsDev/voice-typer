"""Focused config-validation tests for the WAVE3-A03 hook-architecture fix.

Covers three review.md entries:

* **XS-34** — Pre-commit + husky conflict: both ``pre-commit install`` and
  ``npm install`` (via the ``prepare`` script) previously wrote
  ``.git/hooks/pre-commit`` and whichever ran LAST won. The fix: husky is
  the SOLE installer of git hooks (``core.hooksPath = .husky/_/``);
  ``pre-commit install`` writes to ``.git/hooks/`` which git ignores, and
  the pre-commit framework is invoked via ``pre-commit run`` from inside
  husky's ``.husky/pre-commit`` wrapper.

* **XS-35** — Husky pre-push too slow + mypy installs torch (~2GB). The
  remaining fix: convert mypy from a ``mirrors-mypy`` repo entry with
  ``additional_dependencies: [numpy, torch, ...]`` to a LOCAL hook with
  ``language: system`` / ``entry: python -m mypy`` so it reuses the
  project venv and never reinstalls torch.

* **XS-68** — ``typecheck:root`` was a silent no-op (``tsc --noEmit``
  against the solution-style ``tsconfig.json`` doesn't type-check the
  referenced projects). Already fixed: ``typecheck:root`` is now
  ``tsc -b --noEmit`` (build mode, type-checks refs without emitting),
  and ``typecheck`` no longer starts with the no-op ``tsc --noEmit``.
  This test verifies the fix is still in place.
"""

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


# ── XS-34: husky is the sole installer of git hooks ──────────────────────


def test_pre_commit_config_has_no_mirrors_mypy_repo() -> None:
    """``mirrors-mypy`` repo was removed because it created an isolated
    venv and reinstalled torch on every ``pre-commit run mypy``. The mypy
    hook is now a ``local`` hook with ``language: system`` (see XS-35)."""
    cfg = yaml.safe_load(PRE_COMMIT_CONFIG.read_text())
    repos = [r["repo"] for r in cfg["repos"]]
    assert "https://github.com/pre-commit/mirrors-mypy" not in repos, (
        "mirrors-mypy repo should be removed; mypy is now a local hook "
        "with language: system to reuse the project venv (XS-35)."
    )


def test_pre_commit_config_header_documents_husky_sole_installer() -> None:
    """The header comment must explain that husky is the SOLE installer
    of git hooks and that ``pre-commit install`` is NOT used (XS-34)."""
    text = PRE_COMMIT_CONFIG.read_text()
    # Pull just the header comment block (everything before ``repos:``).
    header = text.split("repos:", 1)[0]
    assert "XS-34" in header, "header should reference XS-34 architecture"
    assert "husky" in header.lower()
    assert "pre-commit install" in header.lower()
    assert "core.hooksPath" in header or "hooksPath" in header


def test_husky_pre_commit_invokes_pre_commit_run() -> None:
    """Husky's ``.husky/pre-commit`` must invoke ``pre-commit run`` so
    that the pre-commit framework's hooks (ruff, biome-check, etc.) run
    without needing ``pre-commit install`` to write its own git hook
    (XS-34)."""
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
    """The ``.husky/pre-commit`` header must reference the XS-34 fix and
    explain that husky is the sole installer of git hooks."""
    text = HUSKY_PRE_COMMIT.read_text()
    # Header is the top comment block.
    header = text.split("echo", 1)[0] if "echo" in text else text[:2000]
    assert "XS-34" in header or "sole installer" in header.lower(), (
        ".husky/pre-commit header should reference XS-34 / sole-installer "
        "architecture so future maintainers understand the design."
    )


def test_contributing_tldr_does_not_recommend_pre_commit_install() -> None:
    """The TL;DR must NOT tell contributors to run ``pre-commit install``
    as a primary install step (XS-34). ``npm install`` auto-installs
    husky hooks via the ``prepare`` script; ``pre-commit install`` is a
    misleading no-op (its output is ignored because
    ``core.hooksPath = .husky/_/``)."""
    # CONTRIBUTING.md is UTF-8; the default locale encoding on Windows
    # (cp1252) would raise UnicodeDecodeError on non-ASCII chars.
    text = CONTRIBUTING.read_text(encoding="utf-8")
    # The TL;DR is the first blockquote. Use MULTILINE so ^ matches at
    # the start of each line, and DOTALL so . matches newlines inside
    # the blockquote.
    tldr_match = re.search(
        r"^>\s\*\*TL;DR\*\*.*?(?=\n\n)",
        text,
        re.DOTALL | re.MULTILINE,
    )
    assert tldr_match, "TL;DR blockquote not found in CONTRIBUTING.md"
    tldr = tldr_match.group(0)
    # The TL;DR may mention `pre-commit install` only in a "Do NOT run"
    # warning, not as a recommended install step. The pre-fix TL;DR had
    # the pattern `` `pre-commit install`, then `` (a bare recommendation
    # as part of the install chain). The post-fix TL;DR either omits
    # ``pre-commit install`` entirely or includes it only inside a
    # parenthetical "Do NOT run" warning.
    bare_recommendation = re.search(r"`pre-commit install`,\s*then", tldr)
    assert bare_recommendation is None, (
        "TL;DR should not list `pre-commit install` as a recommended "
        "install step (XS-34). Found TL;DR:\n" + textwrap.indent(tldr, "    ")
    )


def test_contribing_warns_against_pre_commit_install() -> None:
    """CONTRIBUTING.md must contain an explicit "Do NOT run
    ``pre-commit install``" warning so contributors understand the
    single-installer architecture (XS-34)."""
    # CONTRIBUTING.md is UTF-8; the default locale encoding on Windows
    # (cp1252) would raise UnicodeDecodeError on non-ASCII chars.
    text = CONTRIBUTING.read_text(encoding="utf-8")
    assert "Do NOT run `pre-commit install`" in text or ("Do NOT run\n`pre-commit install`" in text), (
        "CONTRIBUTING.md must contain an explicit 'Do NOT run `pre-commit install`' warning (XS-34)."
    )


def test_contributing_documents_mypy_local_hook() -> None:
    """CONTRIBUTING.md must document that mypy is now a LOCAL hook with
    ``language: system`` so contributors know to activate the project
    venv before running ``pre-commit run mypy`` (XS-35)."""
    # CONTRIBUTING.md is UTF-8; the default locale encoding on Windows
    # (cp1252) would raise UnicodeDecodeError on non-ASCII chars.
    text = CONTRIBUTING.read_text(encoding="utf-8")
    assert "language: system" in text, (
        "CONTRIBUTING.md should mention that mypy uses `language: system` to reuse the project venv (XS-35)."
    )
    assert "torch" in text.lower(), "CONTRIBUTING.md should mention torch in the mypy rationale (XS-35)."


# ── XS-35: mypy local hook + pre-push scope ──────────────────────────────


def _find_hook(cfg: dict, hook_id: str) -> dict:
    for repo in cfg["repos"]:
        for hook in repo.get("hooks", []):
            if hook["id"] == hook_id:
                return hook
    raise KeyError(f"hook {hook_id!r} not found in .pre-commit-config.yaml")


def test_mypy_hook_is_local_with_language_system() -> None:
    """The mypy hook must be a ``local`` hook with ``language: system``
    so it reuses the project venv instead of creating an isolated venv
    and reinstalling torch (XS-35)."""
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


def test_mypy_hook_entry_uses_python_dash_m() -> None:
    """The mypy entry must be ``python -m mypy`` (not bare ``mypy``) so
    it works on Windows where ``.venv/Scripts/mypy.exe`` may not be on
    PATH but ``.venv/Scripts/python.exe`` is (XS-35)."""
    cfg = yaml.safe_load(PRE_COMMIT_CONFIG.read_text())
    mypy_hook = _find_hook(cfg, "mypy")
    assert mypy_hook["entry"] == "python -m mypy", (
        "mypy hook entry must be `python -m mypy` for cross-platform "
        "venv portability (XS-35). Found: " + repr(mypy_hook.get("entry"))
    )


def test_mypy_hook_is_at_pre_push_stage() -> None:
    """mypy must stay at ``stages: [pre-push]`` so it does NOT run on
    every commit (XS-35)."""
    cfg = yaml.safe_load(PRE_COMMIT_CONFIG.read_text())
    mypy_hook = _find_hook(cfg, "mypy")
    assert mypy_hook.get("stages") == ["pre-push"], "mypy hook must be at `stages: [pre-push]` (XS-35). Found: " + repr(
        mypy_hook.get("stages")
    )


def test_mypy_hook_files_scoped_to_server() -> None:
    """mypy must scope ``files: ^voice_typer/server/`` so it doesn't
    type-check the client (which has its own tsc-based typecheck)."""
    cfg = yaml.safe_load(PRE_COMMIT_CONFIG.read_text())
    mypy_hook = _find_hook(cfg, "mypy")
    assert mypy_hook.get("files") == "^voice_typer/server/", (
        "mypy hook must scope `files: ^voice_typer/server/`. Found: " + repr(mypy_hook.get("files"))
    )


def test_husky_pre_push_uses_cached_typecheck() -> None:
    """``.husky/pre-push`` must use ``npm run typecheck`` (cached,
    ~5s incremental) and NOT ``npm run typecheck:ci``
    (``tsc -b --force``, cache-busting, 30s-2min) — XS-35.

    The historical ``typecheck:ci`` reference is allowed inside
    backtick-quoted comments (it's part of the rationale); only the
    actual shell invocation matters.
    """
    text = HUSKY_PRE_PUSH.read_text()
    # Strip comment lines (lines starting with `#` or after a `#` in a
    # shell line — but the pre-push file's comments are all on their own
    # `#`-prefixed lines, so we just drop those). This isolates the
    # actual shell commands from the rationale comments.
    code_lines = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    code_only = "\n".join(code_lines)
    assert "npm run typecheck" in code_only, (
        ".husky/pre-push must invoke `npm run typecheck` in its actual "
        "command (XS-35). Code-only content:\n" + textwrap.indent(code_only, "    ")
    )
    # `typecheck:ci` must NOT appear as the actual invocation. Use a
    # word-boundary match so `npm run typecheck` (without `:ci`) is OK.
    ci_invocation = re.search(r"npm run typecheck:ci\b", code_only)
    assert ci_invocation is None, (
        ".husky/pre-push must NOT invoke `npm run typecheck:ci` as the "
        "actual pre-push typecheck (XS-35) — it's cache-busting and was "
        "the original slowness root cause. Code-only content:\n" + textwrap.indent(code_only, "    ")
    )


def test_husky_pre_push_scopes_pytest_to_fast_subset() -> None:
    """``.husky/pre-push`` must scope pytest to the fast subset
    (``-k 'not slow and not integration' -m 'not slow' --timeout=30``)
    so the pre-push run is 2-3 min, not 10-15 min (XS-35)."""
    text = HUSKY_PRE_PUSH.read_text()
    assert "--timeout=30" in text, ".husky/pre-push must use --timeout=30 for pytest (XS-35)."
    assert "not slow" in text, ".husky/pre-push must exclude slow tests (XS-35)."
    assert "not integration" in text, ".husky/pre-push must exclude integration tests (XS-35)."


# ── XS-68: typecheck:root is not a no-op ─────────────────────────────────


def _load_package_json() -> dict:
    return json.loads(PACKAGE_JSON.read_text())


def test_typecheck_root_uses_build_mode() -> None:
    """``typecheck:root`` must use ``tsc -b --noEmit`` (build mode,
    type-checks referenced projects without emitting) — NOT the silent
    no-op ``tsc --noEmit`` (XS-68).

    ``tsconfig.json`` is a solution-style config (``files: []`` +
    only ``references``). Running ``tsc --noEmit`` against it does NOT
    type-check the referenced projects; only ``tsc -b`` does.
    """
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
    """The ``typecheck`` script must NOT start with a bare ``tsc
    --noEmit`` (which is a no-op against the solution-style
    ``tsconfig.json``). The real checks happen in the subsequent
    ``tsc -p tsconfig.web.json`` and ``tsc -p tsconfig.node.json``
    calls (XS-68)."""
    pkg = _load_package_json()
    typecheck = pkg["scripts"]["typecheck"]
    # The bare `tsc --noEmit` no-op is what we're guarding against.
    bare_no_op = typecheck.startswith("tsc --noEmit")
    assert not bare_no_op, (
        "`typecheck` script must NOT start with `tsc --noEmit` — it's "
        "a silent no-op against the solution-style tsconfig.json (XS-68). "
        "Found: " + repr(typecheck)
    )
    # And the real per-project checks must be present.
    assert "tsconfig.web.json" in typecheck, "`typecheck` must include the web project typecheck (XS-68)."
    assert "tsconfig.node.json" in typecheck, "`typecheck` must include the node project typecheck (XS-68)."


def test_tsconfig_root_is_solution_style() -> None:
    """Guard that ``tsconfig.json`` is still a solution-style config
    (``files: []`` + only ``references``). If this ever changes, the
    XS-68 fix's premise (that ``tsc --noEmit`` is a no-op against it)
    no longer holds, and the ``typecheck:root`` / ``typecheck`` scripts
    should be re-evaluated."""
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
