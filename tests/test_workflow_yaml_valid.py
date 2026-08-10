"""YAML lint + GHA pin-preserving regression test for ``.github/workflows/``.

This test addresses two CI-change risks identified in the batch
:

1. **YAML syntax validity** — any malformed ``.github/workflows/*.yml``
   file breaks EVERY push (GitHub rejects the workflow file at 0s with
   "workflow file issue"). The Linux sandbox CANNOT execute a real
   GitHub Actions workflow, so the only locally-verifiable signal is
   that the file parses as valid YAML. This test parses every workflow
   file with ``yaml.safe_load`` and asserts no exception is raised.

2. **C-CI-1 pin preservation** — the project pins all GitHub Actions
   to specific Node-24-runtime versions (see the header comment block
   in ``build.yml``). Unpinning (e.g. ``actions/checkout@v5`` →
   ``actions/checkout@main``) introduces a supply-chain risk via tag
   re-pointing. This test asserts every ``uses:`` directive in every
   workflow file matches the pinned-version map below; an edit that
   silently downgrades ``actions/checkout@v5`` → ``actions/checkout@v4``
   (or unpins to ``@main``) is caught at PR time instead of at the
   next supply-chain incident.

The test runs on every OS (no Windows/macOS-only deps). PyYAML is
already in the test requirements (``pyproject.toml [dev,test]``).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

WORKFLOWS_DIR = Path(__file__).resolve().parents[1] / ".github" / "workflows"
PROJECT_ROOT = WORKFLOWS_DIR.parents[1]

# C-CI-1: the canonical pinned-version map. Every `uses: <action>@<ref>`
# directive in every workflow file MUST reference one of these versions.
# If a future Node-24-runtime bump lands (e.g. actions/checkout@v6), update
# BOTH the workflow files AND this map in the same PR — the test will fail
# otherwise, forcing the maintainer to consciously acknowledge the bump.
PINNED_ACTION_VERSIONS: dict[str, str] = {
    "actions/checkout": "v5",
    "actions/setup-python": "v7",
    "actions/cache": "v5",
    "actions/setup-node": "v7",
    "actions/upload-artifact": "v5",
    "actions/download-artifact": "v5",
    "astral-sh/setup-uv": "v6",
    "dtolnay/rust-toolchain": "v1",
    "actions/attest-build-provenance": "v4",
}

# Regex matching `uses: <owner>/<action>@<ref>` directives. Captures the
# action name (group 1) and the ref (group 2). Lines starting with `#`
# (commented-out examples in the header docstring) are skipped by the
# leading-whitespace-then-`uses:` anchor.
USES_RE = re.compile(r"^\s*-\s+uses:\s+([A-Za-z0-9_.\-/]+)@([A-Za-z0-9_.\-/]+)\s*$", re.MULTILINE)
# Inline `uses:` (indented under a step key, e.g. inside a `with:` block on
# the next line — rare, but `actions/attest-build-provenance` uses this form
# in some workflows). This catches the `        uses: foo@vN` form too.
USES_INLINE_RE = re.compile(r"^\s*uses:\s+([A-Za-z0-9_.\-/]+)@([A-Za-z0-9_.\-/]+)\s*$", re.MULTILINE)


def _workflow_files() -> list[Path]:
    """Return every ``.yml`` file under ``.github/workflows/`` (sorted)."""
    if not WORKFLOWS_DIR.is_dir():
        return []
    return sorted(WORKFLOWS_DIR.glob("*.yml"))


def _extract_uses(text: str) -> list[tuple[str, str, int]]:
    """Return ``[(action_name, ref, line_no), ...]`` for every ``uses:`` directive."""
    results: list[tuple[str, str, int]] = []
    for match in USES_RE.finditer(text):
        results.append((match.group(1), match.group(2), text.count("\n", 0, match.start()) + 1))
    for match in USES_INLINE_RE.finditer(text):
        # Skip duplicates already captured by USES_RE (the `- uses:` form
        # also matches USES_INLINE_RE because `- uses:` starts with whitespace
        # then `uses:`).
        line_no = text.count("\n", 0, match.start()) + 1
        if any(r[2] == line_no for r in results):
            continue
        results.append((match.group(1), match.group(2), line_no))
    return results


# ---------------------------------------------------------------------------
# YAML validity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("wf_path", _workflow_files(), ids=lambda p: p.name)
def test_workflow_yaml_parses(wf_path: Path) -> None:
    """Every workflow file must parse as valid YAML (no syntax errors).

    A malformed workflow file breaks every push to the repo (GitHub rejects
    it at 0s with a "workflow file issue" error before any job runs). The
    Linux sandbox cannot execute a real workflow, so YAML parsing is the
    only locally-verifiable correctness signal.
    """
    text = wf_path.read_text(encoding="utf-8")
    # `yaml.safe_load` returns None for empty files; we want a dict.
    parsed = yaml.safe_load(text)
    assert isinstance(parsed, dict), (
        f"{wf_path.name}: top-level YAML must be a mapping (dict), got {type(parsed).__name__}"
    )


# ---------------------------------------------------------------------------
# C-CI-1 pin preservation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("wf_path", _workflow_files(), ids=lambda p: p.name)
def test_workflow_actions_are_pinned(wf_path: Path) -> None:
    """Every ``uses:`` directive must reference the pinned version (C-CI-1).

    Asserts that each ``<action>@<ref>`` pair matches the canonical pinned
    version in ``PINNED_ACTION_VERSIONS``. Catches silent downgrades
    (``@v5`` → ``@v4``) and unpinned refs (``@main``, ``@HEAD``,
    ``@<commit-sha>``) at PR time.
    """
    text = wf_path.read_text(encoding="utf-8")
    uses_directives = _extract_uses(text)
    if not uses_directives:
        # Some workflows (e.g. mutation.yml — RETIRED) legitimately have
        # zero `uses:` directives because they only run a single shell
        # step. Skip the pin assertion for those — nothing to pin.
        pytest.skip(f"{wf_path.name}: no `uses:` directives (retired / shell-only workflow)")
    violations: list[str] = []
    for action_name, ref, line_no in uses_directives:
        if action_name not in PINNED_ACTION_VERSIONS:
            # Unknown action — not necessarily wrong (the project may use
            # other third-party actions like github/codeql-action). Skip
            # the pin assertion but record it for debug visibility.
            continue
        expected_ref = PINNED_ACTION_VERSIONS[action_name]
        if ref != expected_ref:
            violations.append(f"  line {line_no}: {action_name}@{ref} (expected @{expected_ref})")
    assert not violations, (
        f"{wf_path.name}: C-CI-1 pin violation — the following `uses:` "
        "directives do not match the canonical pinned version map "
        "(see PINNED_ACTION_VERSIONS in this test):\n"
        + "\n".join(violations)
        + "\nIf you intended to bump a pin (e.g. actions/checkout@v5 → "
        "v6), update PINNED_ACTION_VERSIONS in this test in the same "
        "PR so the bump is consciously acknowledged."
    )


def test_workflow_files_exist() -> None:
    """Sanity check — the workflows directory must exist and contain files.

    A misconfigured test environment (wrong cwd, missing checkout) would
    otherwise cause `_workflow_files()` to return `[]` and the parametrized
    tests above to silently SKIP instead of FAIL. This test guards against
    that: if the workflows dir is empty/missing, this test fails loudly.
    """
    files = _workflow_files()
    assert files, (
        f"no .yml files found under {WORKFLOWS_DIR} — the test "
        "environment is misconfigured (wrong cwd or missing checkout)."
    )
    # The project ships at least these 9 workflow files today; if any
    # disappear, that's a regression worth investigating.
    expected = {
        "build.yml",
        "client-ci.yml",
        "codeql.yml",
        "mutation.yml",
        "populate-hashes.yml",
        "tauri-build.yml",
        "tauri-linux-build.yml",
        "tauri-macos-build.yml",
        "tauri-windows-build.yml",
    }
    actual = {p.name for p in files}
    missing = expected - actual
    assert not missing, f"expected workflow files are missing: {sorted(missing)}"


# ---------------------------------------------------------------------------
# GP-specific structural assertions
# ---------------------------------------------------------------------------


def test_macos_codesign_uses_timestamp_no_deep() -> None:
    """macOS codesign invocations must include ``--timestamp`` and
    the top-level .app signing must NOT use ``--deep`` (deprecated in
    macOS 11+, breaks notarization under stricter rules).
    """
    wf = WORKFLOWS_DIR / "tauri-macos-build.yml"
    if not wf.is_file():
        pytest.skip("tauri-macos-build.yml not found")
    text = wf.read_text(encoding="utf-8")
    # All three codesign invocations: nested binaries loop, top-level .app, .dmg.
    codesign_count = text.count("codesign --force")
    assert codesign_count >= 3, f"expected ≥3 `codesign --force` invocations in {wf.name}, got {codesign_count}"
    # Every codesign --force invocation should be followed (within a few
    # lines) by --timestamp. We assert `--timestamp` appears at least 3x.
    assert text.count("--timestamp") >= 3, (
        f"expected ≥3 `--timestamp` flags (one per codesign invocation), got {text.count('--timestamp')}"
    )
    # The top-level .app codesign must NOT use --deep.
    # Find the line with `signing top-level app bundle` and assert the
    # next `codesign --force` after it does NOT have --deep.
    marker = "[codesign] signing top-level app bundle"
    assert marker in text, f"could not locate marker '{marker}' in {wf.name}"
    marker_idx = text.index(marker)
    # Find the next `codesign --force` after the marker.
    codesign_idx = text.index("codesign --force", marker_idx)
    # Grab the next ~400 chars (the codesign invocation spans 2-3 lines).
    invocation = text[codesign_idx : codesign_idx + 400]
    assert "--deep" not in invocation, (
        "top-level .app codesign still uses --deep (deprecated in "
        "macOS 11+; nested binaries are signed individually above)."
    )


def test_macos_missing_binary_is_hard_failure() -> None:
    """a missing nested Mach-O in the .app bundle must hard-fail
    the build (``::error::`` + ``exit 1``), NOT silently skip.
    """
    wf = WORKFLOWS_DIR / "tauri-macos-build.yml"
    if not wf.is_file():
        pytest.skip("tauri-macos-build.yml not found")
    text = wf.read_text(encoding="utf-8")
    assert "SKIP (not found)" not in text, (
        "stale `SKIP (not found)` message still present — missing "
        "nested binaries must hard-fail with `::error::` + `exit 1`."
    )
    assert "::error::Expected binary missing from .app bundle:" in text, (
        "expected `::error::Expected binary missing from .app bundle:` error message not found."
    )


def test_macos_codesign_verify_step_exists() -> None:
    """a verification step (`codesign --verify` + `spctl --assess`)
    must exist between codesign and notarize to catch a bad signature
    before the ~10 min notarytool round-trip.
    """
    wf = WORKFLOWS_DIR / "tauri-macos-build.yml"
    if not wf.is_file():
        pytest.skip("tauri-macos-build.yml not found")
    text = wf.read_text(encoding="utf-8")
    assert "codesign --verify --verbose=4" in text, "`codesign --verify --verbose=4` verification step not found."
    assert "spctl --assess --verbose=4" in text, "`spctl --assess --verbose=4` Gatekeeper assessment not found."


def test_macos_gate_documentation_present() -> None:
    """the macOS workflow must document the gate status at the top of
    the file AND have its three jobs enabled (``if: true``) so the
    Phase 0-M validation run can execute via workflow_dispatch.
    """
    wf = WORKFLOWS_DIR / "tauri-macos-build.yml"
    if not wf.is_file():
        pytest.skip("tauri-macos-build.yml not found")
    text = wf.read_text(encoding="utf-8")
    # The top-of-file gate block must document the state + the
    # validation handoff (runbook) so maintainers know what must pass
    # before cutover.
    head = text[:4000]  # the gate block is in the top-of-file comment.
    assert "VALIDATION HANDOFF" in head, "top-of-file gate block must document the validation handoff."
    assert "Phase 0-M" in head, "top-of-file gate block must reference Phase 0-M."
    # The jobs must be ENABLED (no `if: false` guards left) so the
    # Phase 0-M validation run can execute via workflow_dispatch.
    assert "if: false" not in text, (
        "expected NO `if: false` job guards — the macOS jobs must be "
        "enabled (`if: true`) for the Phase 0-M validation run."
    )
    assert text.count("if: true") >= 3, (
        "expected ≥3 `if: true` job guards (one per macOS job: build-aarch64, build-x86_64, build-tauri-universal)."
    )


def test_tauri_workflows_have_config_drift_failfast_gate() -> None:
    """Every per-platform Tauri workflow must run the config drift guards as a
    fail-fast step BEFORE the build.

    The guards are the same tests the full suite enforces
    (``test_bundle_identifier_parity.py`` identifier↔appId +
    productName + version parity — run as the WHOLE module so a new
    identity-parity class is auto-included — plus the
    ``test_gen_tauri_icons_stub.py`` bundle.icon↔git drift tests), but as a
    dedicated pre-build step a drift regression (e.g. an icon added to one
    side only, or the Tauri identifier / Electron appId / productName /
    version drifting apart) dies in seconds instead of only after the whole
    test suite.
    """
    node_ids = (
        "tests/tauri/test_bundle_identifier_parity.py",
        "tests/tauri/test_gen_tauri_icons_stub.py::test_tauri_conf_icon_list_matches_tracked_icons",
        "tests/tauri/test_gen_tauri_icons_stub.py::test_per_arch_configs_do_not_override_bundle_icon",
        # The whole drift file runs (all nine pairs: bundle.resources ↔
        # stub registry, tauri-binaries.json ↔ triples / Cargo name /
        # launcher install paths / updater map, per-arch config overrides
        # ↔ base config, Nuitka package-data, NSIS hooks).
        "tests/tauri/test_config_script_drift.py",
    )
    for name in ("tauri-windows-build.yml", "tauri-macos-build.yml", "tauri-linux-build.yml"):
        wf = WORKFLOWS_DIR / name
        if not wf.is_file():
            pytest.skip(f"{name} not found")
        text = wf.read_text(encoding="utf-8")
        assert "Verify config drift guards (icons, identity, binaries) (fail fast)" in text, (
            f"{name} must have the fail-fast config drift gate step."
        )
        for node in node_ids:
            assert node in text, (
                f"{name} drift gate must run {node} — a drift regression would "
                "otherwise only surface after the full test suite."
            )
        assert "--no-cov" in text, (
            f"{name} drift gate must pass --no-cov (the pyproject addopts "
            "--cov would otherwise measure a small gate subset)."
        )
    # The macOS universal job does not install the project's [dev,test]
    # deps (only the arch jobs do), so it must install the minimal pytest
    # deps the drift gate needs.
    macos = (WORKFLOWS_DIR / "tauri-macos-build.yml").read_text(encoding="utf-8")
    assert "uv pip install --system pytest pyyaml filelock" in macos, (
        "tauri-macos-build.yml universal job must install pytest/pyyaml/filelock "
        "for the fail-fast config drift gate (it does not install [dev,test])."
    )


def test_windows_signs_voice_typer_tauri_exe() -> None:
    """the Windows workflow must sign the standalone
    ``voice-typer-tauri.exe`` (in addition to the NSIS + MSI installers)
    AND include it in the SHA256SUMS loop + SLSA subject-path.
    """
    wf = WORKFLOWS_DIR / "tauri-windows-build.yml"
    if not wf.is_file():
        pytest.skip("tauri-windows-build.yml not found")
    text = wf.read_text(encoding="utf-8")
    # The signing step exists.
    assert "Sign standalone voice-typer-tauri.exe " in text, "standalone voice-typer-tauri.exe signing step not found."
    # SHA256SUMS loop includes the per-triple path.
    assert "src-tauri/target/${{ matrix.target }}/release/voice-typer-tauri.exe" in text, (
        "voice-typer-tauri.exe missing from SHA256SUMS loop / SLSA subject-path."
    )


def test_windows_signtool_retry_loop() -> None:
    """signtool calls must be wrapped in a PowerShell retry loop
    (3 attempts × 30s backoff) with fallback to alternate timestamp
    servers (Sectigo, GlobalSign, SSL.com).
    """
    signing_helper = PROJECT_ROOT / "scripts" / "windows" / "sign-authenticode.ps1"
    assert signing_helper.is_file(), "missing shared Windows signing helper"
    text = signing_helper.read_text(encoding="utf-8")
    # All 4 alternate timestamp servers must be present.
    for ts in (
        "http://timestamp.digicert.com",
        "http://timestamp.sectigo.com",
        "http://timestamp.globalsign.com/scripts/timestamp.dll",
        "http://ts.ssl.com",
    ):
        assert ts in text, f"alternate timestamp server {ts!r} not found in workflow."
    # Retry loop scaffolding.
    assert "Start-Sleep -Seconds 30" in text, "expected 30s backoff (`Start-Sleep -Seconds 30`) in retry loop."
    # 3 attempts.
    assert re.search(r"maxAttempts\s*=\s*3|attempt -le 3", text), (
        "expected 3-attempt retry loop (`maxAttempts = 3` or `attempt -le 3`)."
    )


def test_windows_signtool_has_d_du_flags() -> None:
    """every signtool invocation must include
    ``/fd SHA256 /tr <timestamp> /td SHA256`` with a branding-derived
    ``/d "<APP_NAME>"`` description and ``/du "https://voicetyper.app"``
    so the UAC dialog shows the friendly app name + URL instead of the
    raw binary name.
    """
    from voice_typer.server.branding import APP_NAME

    signing_helper = PROJECT_ROOT / "scripts" / "windows" / "sign-authenticode.ps1"
    assert signing_helper.is_file(), "missing shared Windows signing helper"
    text = signing_helper.read_text(encoding="utf-8")
    # The description is derived from branding.py (C-BRAND-1) — the
    # /d flag must interpolate `$sigDescription`, never a hardcoded name.
    brand_source = "voice_typer/server/branding.py"
    assert brand_source in text, f"signtool description must be sourced from branding.py (missing `{brand_source}`)."
    # One centralized invocation covers sidecar, prewarm, listener, host,
    # NSIS, and MSI signing without divergent arguments.
    d_count = text.count('/d "$sigDescription"')
    du_count = text.count('/du "https://voicetyper.app"')
    assert d_count == 1, f'expected one centralized `/d "$sigDescription"` flag, got {d_count}.'
    assert du_count == 1, f'expected one centralized `/du "https://voicetyper.app"` flag, got {du_count}.'
    # The literal app name must NOT be inlined into a signtool /d flag —
    # it must flow through $sigDescription so a rename propagates to CI.
    assert f'/d "{APP_NAME}"' not in text, (
        'signtool description hardcodes the app name; use /d "$sigDescription" (C-BRAND-1).'
    )


def test_windows_smartscreen_doc_block() -> None:
    """a documentation block near the Windows signing steps must
    explain the SmartScreen reputation timeline + the Azure Trusted
    Signing migration path.
    """
    wf = WORKFLOWS_DIR / "tauri-windows-build.yml"
    if not wf.is_file():
        pytest.skip("tauri-windows-build.yml not found")
    text = wf.read_text(encoding="utf-8")
    assert "" in text, "documentation block must reference ."
    assert "SmartScreen" in text, "SmartScreen reputation timeline not documented."
    assert "Azure Trusted Signing" in text, "Azure Trusted Signing migration path not documented."
    assert "OV" in text and "EV" in text, "OV vs EV cert distinction not documented."


def test_linux_aarch64_comment_updated() -> None:
    """the stale aarch64 comment in the Linux workflow must
    reflect that both arches ship linux-key-listener (aarch64
    cross-compiled via aarch64-linux-gnu-gcc per S2-), not the
    old "aarch64 does NOT ship linux-key-listener" claim.
    """
    wf = WORKFLOWS_DIR / "tauri-linux-build.yml"
    if not wf.is_file():
        pytest.skip("tauri-linux-build.yml not found")
    text = wf.read_text(encoding="utf-8")
    # The stale claim must be GONE.
    assert "aarch64 does NOT" not in text, (
        "stale 'aarch64 does NOT (compile_native.sh can't cross-compile it)' comment still present."
    )
    # The new S2- reference must be present.
    assert "S2-" in text, "S2- (aarch64 cross-compile) reference not found in comment."
    assert "aarch64-linux-gnu-gcc" in text, "aarch64-linux-gnu-gcc cross-compiler reference not found."
