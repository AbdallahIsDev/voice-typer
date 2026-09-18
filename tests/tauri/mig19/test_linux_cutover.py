"""MIG-1.9 Phase 5: Linux packaging contract (Tauri host only).

The project ships exactly ONE desktop shell: the Tauri Rust host. This
module pins the live Linux packaging contract:

  - ``.github/workflows/tauri-linux-build.yml`` builds the Linux host
    with ``cargo tauri build --target <triple>`` for both ``x86_64`` and
    ``aarch64`` (``fail-fast: false``), uploading the ``.deb`` +
    ``.AppImage`` artifacts; ``.rpm`` is produced via the
    ``bundle.linux.rpm`` config in ``tauri.conf.json``.
  - The Linux ``build`` job is gated to
    ``workflow_dispatch`` / ``workflow_call`` (never a plain
    ``if: true``), so the 30-60 min x2-arch bundle build does not fire
    on every push/PR touching ``src-tauri/**`` (ADR-0020 §15).
  - ``.github/workflows/tauri-build.yml`` runs the fail-fast config-drift
    gate once as a ``validate`` job before fanning out, and its
    ``build-linux`` job passes the ``target``/``sign`` inputs through.

The Linux cutover has **two display-server dimensions** (X11 first, then
Wayland: ``enigo.text()`` works on X11; Wayland needs the
clipboard+Ctrl+V fallback) and **two arch dimensions** (x86_64 first,
then aarch64, aarch64 may defer per ADR-0020 Risk #7).

These tests run on any platform (Linux sandbox included), they only read
static files (``tauri-linux-build.yml``, ``tauri-build.yml``,
``tauri.conf.json``). The actual end-to-end validation (install
.deb/.rpm/AppImage on a real X11 + Wayland host, dictate text, verify the
``runtime=tauri`` log line) can only be performed on a real Linux display
host: see the "VALIDATE ON LINUX HOST" block below.

VALIDATE ON LINUX HOST (X11 + Wayland + both archs):
    # ────────────────────────────────────────────────────────────────────
    # x86_64 X11 host (Ubuntu 22.04 OR Fedora 40 with X11 session)
    # ────────────────────────────────────────────────────────────────────
    1.  cd src-tauri
        cargo tauri build --target x86_64-unknown-linux-gnu
    2.  Install one of the three bundle formats produced under
        ``target/x86_64-unknown-linux-gnu/release/bundle/``:
            sudo dpkg -i bundle/deb/*.deb                       # .deb
            sudo dnf install -y bundle/rpm/*.rpm                # .rpm
            chmod +x bundle/appimage/*.AppImage && ./bundle/appimage/*.AppImage   # AppImage
    3.  Verify the runtime is Tauri:
            ps aux | grep voice-typer    # → 'voice-typer-tauri'
            head -1 ~/.config/voice-typer/voice-typer.log   # → run=tauri version=... target=x86_64-unknown-linux-gnu
    4.  Launch the app, grant mic permission, toggle dictation via the
        global hotkey, dictate text into a foreground window
        (gnome-text-editor). Verify ``enigo.text()`` injects the dictated
        text on X11.
    5.  Verify the postinst-installed udev rule + input group:
            groups | grep input
            ls -l /etc/udev/rules.d/99-voice-typer.rules
    6.  Verify crash isolation: ``kill -9 $(pgrep -f python-sidecar)``
        → UI shows "reconnecting…"; Rust supervisor respawns the sidecar;
        dictation resumes within the backoff window.

    # ────────────────────────────────────────────────────────────────────
    # x86_64 Wayland host (Ubuntu 22.04 OR Fedora 40 with Wayland session)
    # ────────────────────────────────────────────────────────────────────
    7.  Repeat steps 1-6 on a Wayland session
        (``echo $XDG_SESSION_TYPE`` → ``wayland``).
    8.  Verify the clipboard+Ctrl+V fallback path replaces ``enigo.text()``
        on Wayland: dictate text → text is injected via clipboard
        borrow/restore (``clipboard_snapshot.py``). The user's prior
        clipboard contents MUST be restored after the paste.
    9.  Verify wl-clipboard is installed + on PATH:
            which wl-copy wl-paste

    # ────────────────────────────────────────────────────────────────────
    # aarch64 host (native ARM Linux OR qemu-system-aarch64)
    # ────────────────────────────────────────────────────────────────────
    10. Repeat steps 1-6 on aarch64 (Raspberry Pi 4/5, Ampere Altra, or
        ``qemu-system-aarch64``):
            cd src-tauri
            cargo tauri build --target aarch64-unknown-linux-gnu
            sudo dpkg -i target/aarch64-unknown-linux-gnu/release/bundle/deb/*.deb
        If ``python-build-standalone`` aarch64 + CTranslate2 aarch64 wheels +
        glibc pinning prove unstable (ADR-0020 Risk #7), DEFER aarch64 —
        x86_64 Linux ships independently per the "Linux sub-order"
        section.

    Expected: all three installers (.deb, .rpm, AppImage) install cleanly
    on X11 + Wayland + both archs; ``enigo.text()`` works on X11;
    clipboard+Ctrl+V fallback works on Wayland.

References:
- ADR-0020 §"Migration Plan" + §"Phase 5, Validation & cutover"
  + §"Reversibility", docs/adr/0020-desktop-runtime-migration-analysis.md
- docs/migration/linux-validation-runbook.md, Phase 0-L 9-point gate
- .github/workflows/tauri-linux-build.yml, Phase 0-L Linux CI build
  (matrix: x86_64 + aarch64; uploads .deb + .AppImage; .rpm is built via
  ``cargo tauri build`` + ``bundle.linux.rpm`` config but has NO explicit
  upload step: see implementation-gap note in
  ``test_ci_workflow_builds_rpm_via_bundle_config``)
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

# ─── Path resolution ─────────────────────────────────────────────────────────
# tests/tauri/mig19/test_linux_cutover.py → parents[3] = voice-typer project root
PROJECT_ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS = PROJECT_ROOT / ".github" / "workflows"
TAURI_LINUX_BUILD_YML = WORKFLOWS / "tauri-linux-build.yml"
TAURI_BUILD_YML = WORKFLOWS / "tauri-build.yml"
TAURI_CONF = PROJECT_ROOT / "src-tauri" / "tauri.conf.json"


# ─── Module-scoped fixtures (read each static file once) ─────────────────────


@pytest.fixture(scope="module")
def linux_workflow_text() -> str:
    """Read ``tauri-linux-build.yml`` once per module."""
    assert TAURI_LINUX_BUILD_YML.is_file(), f"tauri-linux-build.yml missing: {TAURI_LINUX_BUILD_YML}"
    return TAURI_LINUX_BUILD_YML.read_text()


@pytest.fixture(scope="module")
def tauri_build_orchestrator_text() -> str:
    """Read ``tauri-build.yml`` (the cross-platform orchestrator) once per module."""
    assert TAURI_BUILD_YML.is_file(), f"tauri-build.yml missing: {TAURI_BUILD_YML}"
    return TAURI_BUILD_YML.read_text()


def _yaml_job_block(text: str, job: str) -> str | None:
    """Return the YAML lines of ``jobs.<job>`` up to the next top-level key.

    Line-scanning (NOT a multi-line regex, ambiguous ``[ \t]+.*`` line
    matchers cause catastrophic backtracking on long workflow files). It
    is newline-agnostic (CRLF checkouts) and comment/blank-line safe: the
    block ends at the first line that starts with EXACTLY two spaces
    followed by a key character (4+-space indented content lines, blank
    lines, and ``  # comment`` banners do not terminate the block).
    """
    lines = text.splitlines()
    header = f"  {job}:"
    for idx, line in enumerate(lines):
        if line.rstrip() == header:
            block: list[str] = []
            for sub in lines[idx + 1 :]:
                if len(sub) >= 3 and sub.startswith("  ") and not sub.startswith("   ") and sub[2].isalpha():
                    break
                block.append(sub)
            return "\n".join(block)
    return None


@pytest.fixture(scope="module")
def tauri_conf() -> dict:
    """Load ``tauri.conf.json`` once per module."""
    assert TAURI_CONF.is_file(), f"tauri.conf.json missing: {TAURI_CONF}"
    return json.loads(TAURI_CONF.read_text())


# ─── Tests: CI workflow builds .deb + .rpm + AppImage ────────────────────────


def test_ci_workflow_runs_cargo_tauri_build(linux_workflow_text: str) -> None:
    """The Linux CI workflow must run ``cargo tauri build`` (produces all bundle formats)."""
    assert "cargo tauri build" in linux_workflow_text, (
        "tauri-linux-build.yml must run 'cargo tauri build' to produce bundle artifacts"
    )
    # The build must target the matrix arch's Rust triple.
    assert "--target" in linux_workflow_text, "tauri-linux-build.yml must pass --target <triple> to cargo tauri build"


def test_ci_workflow_uploads_deb_artifact(linux_workflow_text: str) -> None:
    """The Linux CI workflow must upload a .deb artifact."""
    assert "Upload .deb artifact" in linux_workflow_text, (
        "tauri-linux-build.yml must have an 'Upload .deb artifact' step"
    )
    assert "bundle/deb/*.deb" in linux_workflow_text, (
        "tauri-linux-build.yml .deb upload must reference bundle/deb/*.deb"
    )


def test_ci_workflow_uploads_appimage_artifact(linux_workflow_text: str) -> None:
    """The Linux CI workflow must upload a .AppImage artifact."""
    assert "Upload .AppImage artifact" in linux_workflow_text, (
        "tauri-linux-build.yml must have an 'Upload .AppImage artifact' step"
    )
    assert "bundle/appimage/*.AppImage" in linux_workflow_text, (
        "tauri-linux-build.yml AppImage upload must reference bundle/appimage/*.AppImage"
    )


def test_ci_workflow_builds_rpm_via_bundle_config(tauri_conf: dict) -> None:
    """The Linux CI workflow builds .rpm via ``tauri.conf.json`` ``bundle.linux.rpm``.

    ``cargo tauri build`` produces .rpm when ``bundle.targets`` includes
    ``"rpm"`` (or ``"all"``) AND ``bundle.linux.rpm`` is configured. The
    CI workflow does NOT have an explicit "Upload .rpm artifact" step —
    the .rpm IS produced by the build but is NOT uploaded as a CI
    artifact. This is an **implementation gap** (see the final report):
    the release criteria require ".deb + .rpm install cleanly", so the CI
    workflow should upload .rpm alongside .deb + .AppImage. The
    ``tauri.conf.json`` ``bundle.linux.rpm`` config IS the source of truth
    for which formats ``cargo tauri build`` produces.
    """
    bundle = tauri_conf.get("bundle", {})
    assert "linux" in bundle, "tauri.conf.json missing 'bundle.linux'"
    assert "rpm" in bundle["linux"], (
        "tauri.conf.json bundle.linux.rpm must be configured so cargo tauri build "
        "produces .rpm (CI workflow relies on cargo tauri build to produce .rpm)"
    )
    rpm_cfg = bundle["linux"]["rpm"]
    assert isinstance(rpm_cfg, dict), "bundle.linux.rpm must be a dict"
    # Tauri v2 schema uses 'postInstallScript' / 'preRemoveScript' (with
    # the 'Script' suffix). The v1 short forms 'postInstall' / 'preRemove'
    # (no 'Script' suffix) are legacy Tauri v1 keys. Strict v2-only
    # assertions below catch any regression to the v1 names.
    assert "postInstallScript" in rpm_cfg, (
        "bundle.linux.rpm.postInstallScript missing, Tauri v2 requires the 'postInstallScript' key"
    )
    assert "postInstall" not in rpm_cfg, (
        "stale short-form 'postInstall' key present on bundle.linux.rpm, should use Tauri v2 'postInstallScript'"
    )
    assert "preRemoveScript" in rpm_cfg, (
        "bundle.linux.rpm.preRemoveScript missing, Tauri v2 requires the 'preRemoveScript' key"
    )
    assert "preRemove" not in rpm_cfg, (
        "stale short-form 'preRemove' key present on bundle.linux.rpm, should use Tauri v2 'preRemoveScript'"
    )
    rpm_postinst = rpm_cfg["postInstallScript"]
    rpm_prerm = rpm_cfg["preRemoveScript"]
    assert rpm_postinst.endswith("postinst.rpm"), (
        f"bundle.linux.rpm.postInstallScript must reference postinst.rpm, got {rpm_postinst!r}"
    )
    assert rpm_prerm.endswith("prerm.rpm"), (
        f"bundle.linux.rpm.preRemoveScript must reference prerm.rpm, got {rpm_prerm!r}"
    )
    # `bundle.targets` must be "all" OR include "rpm".
    targets = bundle.get("targets")
    if isinstance(targets, str):
        assert targets == "all", f"bundle.targets must be 'all' (or a list including 'rpm'); got {targets!r}"
    elif isinstance(targets, list):
        assert "rpm" in targets or "all" in targets, f"bundle.targets list must include 'rpm' or 'all'; got {targets}"
    else:
        pytest.fail(f"bundle.targets must be a string or list; got {type(targets).__name__}")


def test_ci_workflow_documents_phase_0_l_gate(linux_workflow_text: str) -> None:
    """The Linux CI workflow must document the Phase 0-L gate + display-host requirement."""
    assert "Phase 0-L" in linux_workflow_text, (
        "tauri-linux-build.yml must reference Phase 0-L (the Linux Phase 0 spike gate)"
    )
    # The workflow header must mention that smoke tests need a real Linux
    # display host (X11 + Wayland), this is why the bundle `build` job is
    # gated to workflow_dispatch / the orchestrator's workflow_call until
    # validated on a real host (never a plain `if: true` on push/PR).
    assert "display" in linux_workflow_text.lower() or "X11" in linux_workflow_text, (
        "tauri-linux-build.yml must document that X11/Wayland display-host validation "
        "is required before enabling the workflow"
    )


def test_ci_workflow_documents_x11_and_wayland(linux_workflow_text: str) -> None:
    """The Linux CI workflow must document BOTH X11 AND Wayland (Phase 0-L gate)."""
    # The workflow header comments explain the Phase 0-L gate. Per the
    # release criteria, the gate requires Phase 0-L to pass on a real
    # Linux display host running X11 + Wayland.
    assert "X11" in linux_workflow_text, (
        "tauri-linux-build.yml must mention X11 (Phase 0-L gate requires X11 validation)"
    )
    assert "Wayland" in linux_workflow_text or "wayland" in linux_workflow_text, (
        "tauri-linux-build.yml must mention Wayland (Phase 0-L gate requires Wayland validation)"
    )


def test_ci_workflow_has_gate_status_block_with_9_runbook_checks(
    linux_workflow_text: str,
) -> None:
    """The Linux workflow header must carry a GATE STATUS block mirroring
    macOS's, tracking the 9-point Phase 0-L runbook gate + pass state.

    ``docs/migration/linux-validation-runbook.md`` §"9-Point Validation
    Gate Summary" defines the 9 mandatory checks (externalBin sidecar
    spawn, WS handshake, faster-whisper in the Nuitka exe, enigo
    X11 / clipboard+Ctrl+V Wayland paste, libnotify toast, cooperative
    shutdown, prewarm systemd timer, linux-key-listener toggle,
    single-instance). The workflow header must list them with an
    unchecked ``[ ]`` pass-state marker (``[x]`` once validated on a real
    host) so the status of the Phase 0-L handoff is trackable from the
    workflow file itself, like macOS's GATE STATUS block.
    """
    assert "GATE STATUS" in linux_workflow_text, (
        "tauri-linux-build.yml must carry a GATE STATUS header block mirroring tauri-macos-build.yml"
    )
    # The 10 markers = the "9-Point Validation Gate Summary" line + the 9
    # checklist entries with a pass-state marker.
    assert linux_workflow_text.count("[ ]") >= 9, (
        "GATE STATUS block must list the 9 runbook checks each with a pass-state marker"
    )
    # Every one of the 9 runbook checks must appear by (case-insensitive)
    # keyword so a drifted/missing check is caught.
    for keyword in (
        "externalBin",
        "handshake",
        "faster-whisper",
        "enigo",
        "libnotify",
        "shutdown",
        "prewarm",
        "linux-key-listener",
        "single-instance",
    ):
        assert keyword.lower() in linux_workflow_text.lower(), f"GATE STATUS block must track runbook check: {keyword}"


def test_ci_workflow_enabled_for_phase_0_l_validation(linux_workflow_text: str) -> None:
    """The Linux CI workflow's build job must be enabled for dispatch/orchestrator runs.

    Per ADR-0020 Phase 5, the per-platform Tauri workflow is enabled for
    Phase 0-L validation once it is ready to exercise: the bundle
    ``build`` job runs on ``workflow_dispatch`` / the tauri-build.yml
    orchestrator's ``workflow_call`` ONLY, NEVER on push/PR. A plain
    ``if: true`` would also fire the 30-60 min x2-arch bundle build on
    every push/PR touching ``src-tauri/**`` (the workflow has live
    push/PR triggers for the smoke-cargo-check job), which contradicts
    ADR-0020 §15, so the gate is the event conditional, not ``if: false``
    and not ``if: true``.
    """
    assert "if: false" not in linux_workflow_text, (
        "tauri-linux-build.yml still has an `if: false` job guard, the bundle "
        "build job is enabled for Phase 0-L validation (dispatch/orchestrator)."
    )
    assert (
        "if: github.event_name == 'workflow_dispatch' || github.event_name == 'workflow_call'" in linux_workflow_text
    ), (
        "tauri-linux-build.yml build job must be gated to "
        "`workflow_dispatch || workflow_call` (NOT plain `if: true`, push/PR "
        "triggers would fire the 30-60 min x2-arch bundle build on every push "
        "touching src-tauri/**)."
    )


def test_orchestrator_build_linux_triggers_linux_bundle_job(
    tauri_build_orchestrator_text: str, linux_workflow_text: str
) -> None:
    """``tauri-build.yml``'s ``build-linux`` call must actually trigger the
    Linux bundle job (workflow_call input wiring + gate compatibility).

    The orchestrator fans out via ``workflow_call`` to
    ``tauri-linux-build.yml``; the Linux ``build`` job's event gate is
    ``github.event_name == 'workflow_dispatch' || 'workflow_call'``, and a
    reusable-workflow call from the orchestrator fires exactly the
    ``workflow_call`` event, so ``platform: linux`` (or ``all``) on the
    orchestrator runs the bundle job. This test pins both sides: the
    orchestrator must call the Linux workflow with the
    ``target``/``sign`` inputs wired through, AND the Linux workflow's
    bundle gate must accept the ``workflow_call`` event. If either side
    drifts (orchestrator stops calling it, or the gate forgets
    ``workflow_call``), a ``platform: linux`` dispatch silently no-ops —
    no bundle, no artifacts, no error.
    """
    build_linux_block = _yaml_job_block(tauri_build_orchestrator_text, "build-linux")
    assert build_linux_block, "tauri-build.yml must define a build-linux job that calls the Linux workflow"
    block = build_linux_block
    assert "uses: ./.github/workflows/tauri-linux-build.yml" in block, (
        "build-linux must call tauri-linux-build.yml via workflow_call"
    )
    assert "target: ${{ github.event.inputs.target }}" in block, (
        "build-linux must pass the target input through to the Linux workflow"
    )
    assert "sign: ${{ fromJSON(github.event.inputs.sign) }}" in block, (
        "build-linux must pass the sign input through to the Linux workflow"
    )
    # Gate compatibility: the called workflow's bundle job fires on exactly
    # the event this call produces (workflow_call), not on push/PR.
    assert (
        "if: github.event_name == 'workflow_dispatch' || github.event_name == 'workflow_call'" in linux_workflow_text
    ), (
        "Linux bundle job gate must accept the orchestrator's workflow_call "
        "event, otherwise build-linux dispatches silently produce nothing"
    )


def test_orchestrator_runs_pre_dispatch_drift_gate_before_fan_out(
    tauri_build_orchestrator_text: str,
) -> None:
    """``tauri-build.yml`` must run the fail-fast config-drift gate ONCE as a
    pre-dispatch ``validate`` job before fanning out to all three platforms.

    The per-platform workflows each re-run the gate inside their build jobs,
    but the orchestrator hoists the same gate (the FULL
    ``test_config_script_drift.py`` file + the identity-parity module + the
    icons nodes, plus ``--check-icons``) into a single ``validate`` job that
    every platform call ``needs``, a drift regression (identifier/appId
    parity, bundle.icon↔git lockstep, config↔script registry pairs,
    tauri-binaries.json ↔ canonical triples / launcher install paths,
    per-arch config overrides ↔ base config) aborts the whole fan-out in
    ~1 minute instead of after the first 30-60 min platform build.
    """
    assert re.search(r"^  validate:", tauri_build_orchestrator_text, re.MULTILINE), (
        "tauri-build.yml must define a validate job (pre-dispatch drift gate)"
    )
    assert "Pre-dispatch config drift gate" in tauri_build_orchestrator_text, (
        "tauri-build.yml validate job must be the pre-dispatch drift gate"
    )
    assert "tests/tauri/test_bundle_identifier_parity.py" in tauri_build_orchestrator_text, (
        "validate job must run the identifier↔appId parity drift guard"
    )
    assert "tests/tauri/test_config_script_drift.py" in tauri_build_orchestrator_text, (
        "validate job must run the FULL config↔script drift file (all pair guards)"
    )
    assert "python3 scripts/gen_tauri_icons_stub.py --check-icons" in (tauri_build_orchestrator_text), (
        "validate job must run --check-icons on the committed bundle.icon set"
    )
    # Every platform call must depend on the gate (fan-out is blocked until
    # the gate passes).
    for platform_job in ("build-windows", "build-macos", "build-linux"):
        platform_block = _yaml_job_block(tauri_build_orchestrator_text, platform_job)
        assert platform_block, f"tauri-build.yml must define a {platform_job} job"
        assert "needs: validate" in platform_block, (
            f"{platform_job} must needs: validate, the pre-dispatch drift gate must run once before the fan-out"
        )


def test_ci_workflow_matrix_includes_both_archs(linux_workflow_text: str) -> None:
    """The Linux CI workflow matrix must include BOTH x86_64 AND aarch64.

    ADR-0020 §"Reversibility" + the "Linux sub-order" section mandate
    that x86_64 + aarch64 are independent (aarch64 may defer if
    unstable). The CI matrix must build both in parallel with
    ``fail-fast: false`` so an aarch64 failure doesn't hide an x86_64
    success.
    """
    assert "x86_64" in linux_workflow_text, "tauri-linux-build.yml matrix must include x86_64"
    assert "aarch64" in linux_workflow_text, "tauri-linux-build.yml matrix must include aarch64"
    # The matrix.arch array must list both.
    matrix_match = re.search(
        r"matrix:\s*\n\s*arch:\s*\n((?:\s*-\s*\S+\s*\n?)+)",
        linux_workflow_text,
    )
    assert matrix_match, "could not extract matrix.arch block from tauri-linux-build.yml"
    arch_block = matrix_match.group(1)
    assert "x86_64" in arch_block, "matrix.arch must include x86_64"
    assert "aarch64" in arch_block, "matrix.arch must include aarch64"
    # fail-fast: false so each arch's result is independent.
    assert "fail-fast: false" in linux_workflow_text, (
        "tauri-linux-build.yml must set fail-fast: false so an aarch64 build failure "
        "doesn't hide an x86_64 success (per ADR-0020 §Reversibility)"
    )


def test_ci_workflow_documents_aarch64_cross_compile(linux_workflow_text: str) -> None:
    """The Linux CI workflow must document the aarch64 cross-compile path (qemu).

    ADR-0020 §4.4 mandates a separate Nuitka build per target triple
    (Nuitka does NOT cross-compile natively). The aarch64 build uses
    ``qemu-user-static`` + ``binfmt_misc`` to execute the aarch64
    python-build-standalone interpreter during compilation on the x86_64
    CI runner. This must be documented in the workflow header.
    """
    assert "qemu" in linux_workflow_text.lower(), (
        "tauri-linux-build.yml must document the qemu-user-static aarch64 cross-compile path"
    )
    assert "binfmt" in linux_workflow_text.lower(), (
        "tauri-linux-build.yml must document the binfmt_misc registration for aarch64"
    )
