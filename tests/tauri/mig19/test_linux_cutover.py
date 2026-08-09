"""MIG-1.9 Phase 5 — Linux cutover validation.

Validates the Linux portion of the Electron → Tauri cutover plan per
ADR-0020 Phase 5 + ``docs/migration/cutover-playbook.md``. Linux is the
3rd platform to cut over (after Windows + macOS). The Linux cutover has
**two display-server dimensions** (X11 first, then Wayland — `enigo.text()`
works on X11; Wayland needs the clipboard+Ctrl+V fallback) and **two arch
dimensions** (x86_64 first, then aarch64 — aarch64 may defer per
ADR-0020 Risk #7). The cutover is **reversible**: the Electron build path
stays intact and shippable on every platform throughout — Tauri is
strictly additive until the platform's cutover gate (Phase 0-L) is met.

These tests run on any platform (Linux sandbox included) — they only read
static files (``cutover-playbook.md``, ``tauri-linux-build.yml``,
``electron-builder.yml``, ``tauri.conf.json``). The actual end-to-end
cutover validation (install .deb/.rpm/AppImage on a real X11 + Wayland
host, dictate text, verify the ``runtime=tauri`` log line, rollback to
Electron) can only be performed on a real Linux display host — see the
"VALIDATE ON LINUX HOST" block below.

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
    3.  Verify the runtime is Tauri (NOT Electron):
            ps aux | grep voice-typer    # → 'voice-typer-tauri' (Tauri) vs 'voice-typer' (Electron)
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
    7.  Verify rollback to Electron (reversible fallback):
            sudo apt remove voice-typer   # OR: sudo dnf remove voice-typer
            # Download the Electron .deb/.rpm/AppImage from the same release page.
            sudo dpkg -i voice-typer-<ver>-linux-x86_64.deb
            ps aux | grep voice-typer      # → 'voice-typer' (Electron, NOT 'voice-typer-tauri')
            head -1 ~/.config/voice-typer/voice-typer.log   # → 'runtime=electron ...'
            # Verify history DB / vocabulary / templates / settings persisted across rollback.

    # ────────────────────────────────────────────────────────────────────
    # x86_64 Wayland host (Ubuntu 22.04 OR Fedora 40 with Wayland session)
    # ────────────────────────────────────────────────────────────────────
    8.  Repeat steps 1-7 on a Wayland session
        (``echo $XDG_SESSION_TYPE`` → ``wayland``).
    9.  Verify the clipboard+Ctrl+V fallback path replaces ``enigo.text()``
        on Wayland: dictate text → text is injected via clipboard
        borrow/restore (``clipboard_snapshot.py``). The user's prior
        clipboard contents MUST be restored after the paste.
    10. Verify wl-clipboard is installed + on PATH:
            which wl-copy wl-paste

    # ────────────────────────────────────────────────────────────────────
    # aarch64 host (native ARM Linux OR qemu-system-aarch64)
    # ────────────────────────────────────────────────────────────────────
    11. Repeat steps 1-7 on aarch64 (Raspberry Pi 4/5, Ampere Altra, or
        ``qemu-system-aarch64``):
            cd src-tauri
            cargo tauri build --target aarch64-unknown-linux-gnu
            sudo dpkg -i target/aarch64-unknown-linux-gnu/release/bundle/deb/*.deb
        If ``python-build-standalone`` aarch64 + CTranslate2 aarch64 wheels +
        glibc pinning prove unstable (ADR-0020 Risk #7), DEFER aarch64 —
        x86_64 Linux can cut over independently per the playbook's
        "Linux sub-order" section.

    Expected: all three installers (.deb, .rpm, AppImage) install cleanly
    on X11 + Wayland + both archs; ``enigo.text()`` works on X11;
    clipboard+Ctrl+V fallback works on Wayland; rollback to Electron
    restores the prior runtime without data loss (history DB / vocabulary
    / templates / settings / models all carry over in both directions).

References:
- ADR-0020 §"Migration Plan" + §"Phase 5 — Validation & cutover"
  + §"Reversibility" — docs/adr/0020-desktop-runtime-migration-analysis.md
- docs/migration/cutover-playbook.md — per-platform cutover procedure
  (Linux section: "Linux sub-order (X11 before Wayland, x86_64 before
  aarch64)")
- docs/migration/linux-validation-runbook.md — Phase 0-L 9-point gate
- .github/workflows/tauri-linux-build.yml — Phase 0-L Linux CI build
  (matrix: x86_64 + aarch64; uploads .deb + .AppImage; .rpm is built via
  ``cargo tauri build`` + ``bundle.linux.rpm`` config but has NO explicit
  upload step — see implementation-gap note in
  ``test_ci_workflow_builds_rpm_via_bundle_config``)
- voice_typer/client/electron-builder.yml — Electron fallback config
  (Linux ``target: [AppImage, deb, rpm]`` stays intact for rollback)
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

# ─── Path resolution ─────────────────────────────────────────────────────────
# tests/tauri/mig19/test_linux_cutover.py → parents[3] = voice-typer project root
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DOCS_MIGRATION = PROJECT_ROOT / "docs" / "migration"
CUTOVER_PLAYBOOK = DOCS_MIGRATION / "cutover-playbook.md"
WORKFLOWS = PROJECT_ROOT / ".github" / "workflows"
TAURI_LINUX_BUILD_YML = WORKFLOWS / "tauri-linux-build.yml"
TAURI_BUILD_YML = WORKFLOWS / "tauri-build.yml"
ELECTRON_BUILDER_YML = PROJECT_ROOT / "voice_typer" / "client" / "electron-builder.yml"
TAURI_CONF = PROJECT_ROOT / "src-tauri" / "tauri.conf.json"


# ─── Module-scoped fixtures (read each static file once) ─────────────────────


@pytest.fixture(scope="module")
def playbook_text() -> str:
    """Read ``cutover-playbook.md`` once per module."""
    assert CUTOVER_PLAYBOOK.is_file(), f"cutover playbook missing: {CUTOVER_PLAYBOOK}"
    return CUTOVER_PLAYBOOK.read_text()


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

    Line-scanning (NOT a multi-line regex — ambiguous ``[ \t]+.*`` line
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
def electron_builder_text() -> str:
    """Read ``electron-builder.yml`` once per module."""
    assert ELECTRON_BUILDER_YML.is_file(), f"electron-builder.yml missing: {ELECTRON_BUILDER_YML}"
    return ELECTRON_BUILDER_YML.read_text()


@pytest.fixture(scope="module")
def tauri_conf() -> dict:
    """Load ``tauri.conf.json`` once per module."""
    assert TAURI_CONF.is_file(), f"tauri.conf.json missing: {TAURI_CONF}"
    return json.loads(TAURI_CONF.read_text())


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _extract_linux_block(yml_text: str) -> str:
    """Extract the top-level ``linux:`` block from electron-builder.yml.

    The block starts at ``^linux:`` and runs until the next column-0 token
    (a top-level key like ``deb:`` / ``rpm:`` or a column-0 comment like
    ``# GAP-3:``). All lines inside the block start with whitespace.
    """
    m = re.search(
        r"^linux:\s*\n((?:[ \t]+.*\n|[ \t]*\n)+)",
        yml_text,
        re.MULTILINE,
    )
    assert m, "could not extract top-level 'linux:' block from electron-builder.yml"
    return m.group(1)


def _extract_linux_target_entries(yml_text: str) -> list[str]:
    """Extract the ``target:`` list entries from the ``linux:`` block.

    Supports both the multi-line form (``target:\\n  - AppImage\\n  - deb``)
    and the inline form (``target: [AppImage, deb, rpm]``).
    """
    linux_block = _extract_linux_block(yml_text)
    # Multi-line form first (what the current electron-builder.yml uses).
    multi = re.search(
        r"^[ \t]+target:[ \t]*\n((?:[ \t]+-[ \t]+\S[^\n]*\n)+)",
        linux_block,
        re.MULTILINE,
    )
    if multi:
        return re.findall(r"^[ \t]+-[ \t]+(\S+)", multi.group(1), re.MULTILINE)
    # Inline form: target: [AppImage, deb, rpm]
    inline = re.search(r"^[ \t]+target:[ \t]*\[([^\]]+)\]", linux_block, re.MULTILINE)
    if inline:
        return [t.strip() for t in inline.group(1).split(",") if t.strip()]
    return []


# ─── Tests: cutover playbook documents Linux cutover (X11 + Wayland + both archs) ──


def test_playbook_documents_linux_in_cutover_order(playbook_text: str) -> None:
    """The cutover-playbook must list Linux as the 3rd platform in the order table."""
    assert "Linux" in playbook_text, "playbook must mention Linux"
    # The per-platform cutover order table lists Linux as 3rd.
    assert re.search(r"^\|\s*3rd\s*\|\s*Linux\b", playbook_text, re.MULTILINE), (
        "playbook must list Linux as the 3rd platform in the cutover order table"
    )


def test_playbook_documents_x11_and_wayland(playbook_text: str) -> None:
    """Linux cutover must document BOTH X11 and Wayland display-server dimensions."""
    assert "X11" in playbook_text, "playbook must mention X11 for the Linux cutover"
    assert "Wayland" in playbook_text, "playbook must mention Wayland for the Linux cutover"
    # The "Linux sub-order" section explicitly addresses X11 before Wayland.
    assert "X11 before Wayland" in playbook_text, "playbook must document the X11-before-Wayland cutover sub-order"


def test_playbook_documents_both_archs(playbook_text: str) -> None:
    """Linux cutover must document BOTH x86_64 and aarch64 arch dimensions."""
    assert "x86_64" in playbook_text, "playbook must mention x86_64 for Linux"
    assert "aarch64" in playbook_text, "playbook must mention aarch64 for Linux"
    # The sub-order explicitly addresses x86_64 before aarch64.
    assert "x86_64 before aarch64" in playbook_text, (
        "playbook must document the x86_64-before-aarch64 cutover sub-order"
    )


def test_playbook_documents_phase_0_l_gate(playbook_text: str) -> None:
    """Linux cutover gate must reference Phase 0-L (the Linux Phase 0 spike)."""
    assert "Phase 0-L" in playbook_text, "playbook must reference Phase 0-L as the Linux cutover gate"
    # The cutover order table's "Phase 0 gate" column for Linux must be
    # "Phase 0-L".
    linux_row = re.search(r"^\|\s*3rd\s*\|\s*Linux\b.*$", playbook_text, re.MULTILINE)
    assert linux_row, "could not find Linux row in cutover order table"
    assert "Phase 0-L" in linux_row.group(0), (
        f"Linux row in cutover order table must list 'Phase 0-L' as the gate; got: {linux_row.group(0)!r}"
    )


def test_playbook_has_linux_suborder_section(playbook_text: str) -> None:
    """The playbook must have a dedicated 'Linux sub-order' section.

    That section details the X11-before-Wayland + x86_64-before-aarch64
    ordering + the aarch64 deferral escape hatch (ADR-0020 Risk #7).
    """
    assert "Linux sub-order" in playbook_text, (
        "playbook must have a 'Linux sub-order' section detailing the X11/Wayland + arch ordering"
    )
    # The sub-order section must mention the aarch64 deferral escape hatch.
    sub_idx = playbook_text.index("Linux sub-order")
    sub_section = playbook_text[sub_idx : sub_idx + 1200]
    assert "aarch64" in sub_section, "Linux sub-order section must mention aarch64"
    assert "Defer" in sub_section or "defer" in sub_section, (
        "Linux sub-order section must document the aarch64 deferral escape hatch"
    )


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
    the playbook's hard-criteria §8 requires ".deb + .rpm install
    cleanly", so the CI workflow should upload .rpm alongside .deb +
    .AppImage. The ``tauri.conf.json`` ``bundle.linux.rpm`` config IS
    the source of truth for which formats ``cargo tauri build`` produces.
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
        "bundle.linux.rpm.postInstallScript missing — Tauri v2 requires the 'postInstallScript' key"
    )
    assert "postInstall" not in rpm_cfg, (
        "stale short-form 'postInstall' key present on bundle.linux.rpm — should use Tauri v2 'postInstallScript'"
    )
    assert "preRemoveScript" in rpm_cfg, (
        "bundle.linux.rpm.preRemoveScript missing — Tauri v2 requires the 'preRemoveScript' key"
    )
    assert "preRemove" not in rpm_cfg, (
        "stale short-form 'preRemove' key present on bundle.linux.rpm — should use Tauri v2 'preRemoveScript'"
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


# ─── Tests: Electron fallback preserved (Linux AppImage/deb/rpm in electron-builder.yml) ──


def test_electron_builder_has_linux_section(electron_builder_text: str) -> None:
    """electron-builder.yml must keep a top-level ``linux:`` section (Electron fallback)."""
    assert re.search(r"^linux:\s*$", electron_builder_text, re.MULTILINE), (
        "electron-builder.yml must have a top-level 'linux:' section (Electron fallback)"
    )


def test_electron_builder_linux_target_includes_all_three_formats(
    electron_builder_text: str,
) -> None:
    """The Electron Linux ``target:`` list must include AppImage + deb + rpm.

    Per the cutover playbook Step 2.2, the Electron build PATH stays in
    the repo (reversible fallback) — only the active ``target:`` entries
    are DISABLED (commented out) at cutover time. All three Linux formats
    (AppImage, deb, rpm) must be listed in the ``linux.target`` array so
    the Electron fallback can ship any of them if the Tauri cutover is
    rolled back.
    """
    entries = _extract_linux_target_entries(electron_builder_text)
    assert entries, "could not extract any target: entries from electron-builder.yml linux: block"
    for fmt in ("AppImage", "deb", "rpm"):
        assert fmt in entries, (
            f"electron-builder.yml linux.target must include '{fmt}' "
            f"(Electron fallback must ship all three Linux formats); "
            f"got entries={entries}"
        )


def test_electron_builder_linux_target_entries_not_commented_out(
    electron_builder_text: str,
) -> None:
    """The Electron Linux ``target:`` entries must NOT be commented out (cutover not yet flipped).

    Per the cutover playbook Step 2.2, the cutover flip COMMENTS OUT the
    platform's ``target:`` entries in electron-builder.yml. As of MIG-1.9
    Phase 5 (Linux cutover validation), the Linux cutover has NOT yet
    happened — the Electron fallback must remain the active shipping path
    (``target:`` entries NOT commented out). This test will START FAILING
    once the Linux cutover is flipped (which is the correct signal — the
    test should be removed at that point).
    """
    linux_block = _extract_linux_block(electron_builder_text)
    # Find every `  - AppImage` / `  - deb` / `  - rpm` line in the linux block.
    target_item_lines = re.findall(
        r"^([ \t]+-[ \t]+(?:AppImage|deb|rpm)[ \t]*)$",
        linux_block,
        re.MULTILINE,
    )
    assert target_item_lines, (
        "could not find uncommented `  - AppImage` / `  - deb` / `  - rpm` entries in electron-builder.yml linux: block"
    )
    # Every target item line must be uncommented (no leading `#`).
    for line in target_item_lines:
        stripped = line.lstrip()
        assert not stripped.startswith("#"), (
            f"electron-builder.yml linux.target entry must NOT be commented out "
            f"(Linux cutover not yet flipped): {line!r}"
        )


def test_electron_builder_linux_extra_resources_preserved(
    electron_builder_text: str,
) -> None:
    """The Electron Linux ``extraResources:`` must remain (Python backend embedded).

    The Electron fallback must remain functional — the PyInstaller-built
    Python backend (``voice_typer/dist/voice-typer-backend/``) must still
    be embedded into the .deb/.rpm/AppImage. Without this, the Electron
    fallback silently fails to start (no venv on a fresh install).
    """
    linux_block = _extract_linux_block(electron_builder_text)
    assert "extraResources:" in linux_block, (
        "electron-builder.yml linux: block must keep 'extraResources:' for the Python backend"
    )
    assert "voice-typer-backend" in linux_block, (
        "electron-builder.yml linux.extraResources must reference 'voice-typer-backend'"
    )


def test_electron_builder_deb_and_rpm_sections_preserved(
    electron_builder_text: str,
) -> None:
    """electron-builder.yml must keep top-level ``deb:`` + ``rpm:`` sections.

    The ``deb:`` section wires ``afterInstall: resources/linux/postinst`` +
    ``afterRemove: resources/linux/prerm``; the ``rpm:`` section wires the
    .rpm variants. These must remain so the Electron fallback can install
    the udev rule + input group + Caps Lock neutralization on rollback.
    """
    assert re.search(r"^deb:\s*$", electron_builder_text, re.MULTILINE), (
        "electron-builder.yml must keep a top-level 'deb:' section (Electron fallback)"
    )
    assert re.search(r"^rpm:\s*$", electron_builder_text, re.MULTILINE), (
        "electron-builder.yml must keep a top-level 'rpm:' section (Electron fallback)"
    )
    assert "postinst" in electron_builder_text, "electron-builder.yml deb/rpm sections must reference postinst scripts"
    assert "prerm" in electron_builder_text, "electron-builder.yml deb/rpm sections must reference prerm scripts"


# ─── Tests: Linux cutover gate requires Phase 0-L on X11 + Wayland + both archs ──


def test_ci_workflow_documents_phase_0_l_gate(linux_workflow_text: str) -> None:
    """The Linux CI workflow must document the Phase 0-L gate + display-host requirement."""
    assert "Phase 0-L" in linux_workflow_text, (
        "tauri-linux-build.yml must reference Phase 0-L (the Linux Phase 0 spike gate)"
    )
    # The workflow header must mention that smoke tests need a real Linux
    # display host (X11 + Wayland) — this is why the bundle `build` job is
    # gated to workflow_dispatch / the orchestrator's workflow_call until
    # validated on a real host (never a plain `if: true` on push/PR).
    assert "display" in linux_workflow_text.lower() or "X11" in linux_workflow_text, (
        "tauri-linux-build.yml must document that X11/Wayland display-host validation "
        "is required before enabling the workflow"
    )


def test_ci_workflow_documents_x11_and_wayland(linux_workflow_text: str) -> None:
    """The Linux CI workflow must document BOTH X11 AND Wayland (Phase 0-L gate)."""
    # The workflow header comments explain the Phase 0-L gate. Per the
    # cutover playbook, the gate requires Phase 0-L to pass on a real
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

    Per ADR-0020 Phase 5 + the cutover playbook, the per-platform Tauri
    workflow is enabled for Phase 0-L validation once it is ready to
    exercise: the bundle ``build`` job runs on ``workflow_dispatch`` /
    the tauri-build.yml orchestrator's ``workflow_call`` ONLY — NEVER on
    push/PR. A plain ``if: true`` would also fire the 30-60 min x2-arch
    bundle build on every push/PR touching ``src-tauri/**`` (the workflow
    has live push/PR triggers for the smoke-cargo-check job), which
    contradicts ADR-0020 §15 — so the gate is the event conditional, not
    ``if: false`` and not ``if: true``.
    """
    assert "if: false" not in linux_workflow_text, (
        "tauri-linux-build.yml still has an `if: false` job guard — the bundle "
        "build job is enabled for Phase 0-L validation (dispatch/orchestrator)."
    )
    assert (
        "if: github.event_name == 'workflow_dispatch' || github.event_name == 'workflow_call'" in linux_workflow_text
    ), (
        "tauri-linux-build.yml build job must be gated to "
        "`workflow_dispatch || workflow_call` (NOT plain `if: true` — push/PR "
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
    ``workflow_call`` event — so ``platform: linux`` (or ``all``) on the
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
        "event — otherwise build-linux dispatches silently produce nothing"
    )


def test_orchestrator_runs_pre_dispatch_drift_gate_before_fan_out(
    tauri_build_orchestrator_text: str,
) -> None:
    """``tauri-build.yml`` must run the fail-fast config-drift gate ONCE as a
    pre-dispatch ``validate`` job before fanning out to all three platforms.

    The per-platform workflows each re-run the gate inside their build jobs,
    but the orchestrator hoists the same 6 pytest nodes + ``--check-icons``
    into a single ``validate`` job that every platform call ``needs`` — a
    drift regression (identifier/appId parity, bundle.icon↔git lockstep,
    config↔script registry pairs, tauri-binaries.json ↔ canonical triples)
    aborts the whole fan-out in ~1 minute instead of after the first
    30-60 min platform build.
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
    assert (
        "tests/tauri/test_config_script_drift.py::TestBundleBinariesVsStubRegistry::test_config_declares_exactly_the_stub_generator_registry"
        in tauri_build_orchestrator_text
    ), "validate job must run the config↔stub-registry drift guard"
    assert "python3 scripts/gen_tauri_icons_stub.py --check-icons" in (tauri_build_orchestrator_text), (
        "validate job must run --check-icons on the committed bundle.icon set"
    )
    # Every platform call must depend on the gate (fan-out is blocked until
    # the gate passes).
    for platform_job in ("build-windows", "build-macos", "build-linux"):
        platform_block = _yaml_job_block(tauri_build_orchestrator_text, platform_job)
        assert platform_block, f"tauri-build.yml must define a {platform_job} job"
        assert "needs: validate" in platform_block, (
            f"{platform_job} must needs: validate — the pre-dispatch drift gate must run once before the fan-out"
        )


def test_ci_workflow_matrix_includes_both_archs(linux_workflow_text: str) -> None:
    """The Linux CI workflow matrix must include BOTH x86_64 AND aarch64.

    ADR-0020 §"Reversibility" + the cutover playbook's "Linux sub-order"
    section mandate that x86_64 + aarch64 are independent (aarch64 may
    defer if unstable). The CI matrix must build both in parallel with
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


# ─── Tests: cutover is reversible ────────────────────────────────────────────


def test_playbook_documents_rollback_procedure(playbook_text: str) -> None:
    """The playbook must document a per-platform rollback procedure."""
    assert "Rollback procedure" in playbook_text, "playbook must have a 'Rollback procedure' section"
    assert "Rollback is per-platform" in playbook_text, (
        "playbook must state that rollback is per-platform (Linux rollback does NOT roll back Windows/macOS)"
    )


def test_playback_documents_electron_fallback_preserved(playbook_text: str) -> None:
    """The playbook must state the Electron path stays intact (reversible fallback)."""
    # The intro states: "The Electron build path stays intact and shippable
    # on every platform throughout — Tauri is strictly additive until the
    # platform's cutover gate is met."
    text_lower = playbook_text.lower()
    assert "intact" in text_lower and "shippable" in text_lower, (
        "playbook must state the Electron build path stays intact + shippable (reversible fallback)"
    )
    # Step 2.2 explicitly says: "The Electron build PATH stays in the repo
    # (reversible fallback) — only the active target is disabled."
    assert "reversible fallback" in text_lower, (
        "playbook Step 2.2 must call out 'reversible fallback' for the Electron path"
    )


def test_playbook_documents_data_persistence_on_rollback(playbook_text: str) -> None:
    """The playbook must document that data persists across rollback (no data loss).

    The "What does NOT change on rollback" section must call out: history
    DB, vocabulary, templates, automation, models, settings — all carry
    over in both directions (Electron→Tauri→Electron). This is the
    reversibility guarantee that lets a Linux user flip back to Electron
    without losing their dictation history.
    """
    assert "What does NOT change on rollback" in playbook_text, (
        "playbook must have a 'What does NOT change on rollback' section"
    )
    no_change_idx = playbook_text.index("What does NOT change on rollback")
    # Take the next ~1000 chars to capture the bullet list.
    section = playbook_text[no_change_idx : no_change_idx + 1000].lower()
    for required in ("history", "vocabulary", "templates", "settings", "models"):
        assert required in section, f"playbook rollback section must mention '{required}' persists across rollback"


def test_playbook_documents_rollback_steps(playbook_text: str) -> None:
    """The rollback procedure must enumerate the re-enable + disable + hotfix steps.

    Per the playbook "To roll back a platform that was just cut over":
    1. Re-enable the electron-builder target for that platform.
    2. Disable the per-platform Tauri workflow's top-level ``if:`` guard.
    3. Tag a hotfix release.
    """
    assert "Re-enable the electron-builder target" in playbook_text, (
        "playbook rollback step 1 must say 'Re-enable the electron-builder target'"
    )
    assert "Disable the per-platform Tauri workflow" in playbook_text, (
        "playbook rollback step 2 must say 'Disable the per-platform Tauri workflow'"
    )
    assert "hotfix" in playbook_text.lower(), "playbook rollback step 3 must mention tagging a hotfix release"


def test_playbook_documents_mixed_mode_support(playbook_text: str) -> None:
    """The playbook must document mixed-mode support (Electron + Tauri coexist).

    During the transition, some Linux users are on Electron and some on
    Tauri — both builds read + write the same data dir, so users can
    switch freely. The playbook must document how to tell which build a
    user is on (Linux: ``ps aux | grep voice-typer`` shows
    ``voice-typer-tauri`` vs ``voice-typer``).
    """
    assert "Mixed-mode" in playbook_text or "mixed-mode" in playbook_text, (
        "playbook must have a 'Mixed-mode period' section"
    )
    assert "voice-typer-tauri" in playbook_text, (
        "playbook must document the Linux process-name difference "
        "('voice-typer-tauri' for Tauri vs 'voice-typer' for Electron)"
    )
