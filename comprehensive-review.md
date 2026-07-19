# Comprehensive Review — Open Findings (verified-fixed items removed)

> **Platform warning:** The cloud agent's SUMMARY claimed "all tests pass on Linux." Results in this file tagged **Windows (win32)** are reproduced on this runner and contradict the Linux-only claims. Do NOT trust a Linux-only pass as proof of cross-platform cutover.

---

## Quick-Win Batch (LOW RISK, HIGH VALUE — fix first)

### QW-1 — `pyproject.toml` missing `torch` extra (DEP-2)
- **Severity**: Medium
- **Status**: Pending
- **Description**: `torch==2.13.0` is in `requirements-lock.txt` (line 1213) but not declared in `pyproject.toml`. 6 source files import `torch` (all `try/except ImportError` guarded, so genuinely optional): `transcription.py`, `audio_filters/noise_suppressor.py`, `parakeet_engine.py`, `dictation_pipeline.py`, `crash_recovery.py`, `vad.py`.
- **Recommended fix**: Add `torch = ["torch>=2.0"]` to `[project.optional-dependencies]`; add `torch>=2.0` to `deepfilternet` extra.
- **Effort**: 1h.

### QW-2 — Tauri v2 config key mismatch (8 test failures)
- **Severity**: High (8 pre-existing test failures)
- **Status**: Pending
- **Description**: `src-tauri/tauri.conf.json` lines 73-74, 78-79 use Tauri v1 keys `postInstallScript` / `preRemoveScript`; tests in `tests/tauri/mig17/mig18/mig19` expect Tauri v2 keys `postInstall` / `preRemove`. Tests fail because config field is `None` when read.
- **Affected tests** (8): `mig17/test_autostart_installer_linux.py::test_tauri_conf_has_linux_deb_postinstall`, `::test_tauri_conf_has_linux_deb_preremove`, `mig17/test_externalbin_spawn_linux.py::test_tauri_conf_linux_bundle_uses_postinst_prerm`, `mig17/test_native_key_listener_linux.py::TestTauriBundleResources::test_tauri_conf_linux_deb_uses_postinst_script`, `mig18/test_linux_signing.py::test_deb_post_install_script_wired`, `::test_deb_pre_remove_script_wired`, `::test_rpm_postinst_prerm_exist_and_wired`, `mig19/test_linux_cutover.py::test_ci_workflow_builds_rpm_via_bundle_config`.
- **Recommended fix**: Rename keys in `tauri.conf.json` from `postInstallScript`→`postInstall`, `preRemoveScript`→`preRemove` (Tauri v2 schema). 1-line change × 4 sites.
- **Effort**: 1h.







#### ARCH-5 — `service.py` (2,116 lines): 66-method facade
- **Severity**: Medium
- **Status**: Pending
- **Description**: `VoiceTyperService` exposes 66 total methods (1 `__init__` + 65 public). 21 pure delegation, 44 with real logic. 16 section comment headers span 8 domains (history, model, onboarding, microphone_test, vocabulary, template, status, dictation).
- **Investigation**: VERIFIED. `inspect.getsource(VoiceTyperService.apply_config)` follows `__func__` to defining module — works through mixin inheritance. `hasattr(VoiceTyperService, "test_llm_connection")` works via MRO. Only 6 source-file-read assertions need updating.
- **Mixin approach is safe**: No monkeypatch-by-path blockers unlike ARCH-2/4. Re-exports in `__init__.py` will preserve all 65 public names.
- **Recommended fix**: Split into `voice_typer/server/service/{history,model,onboarding,microphone_test,vocabulary,template,status,dictation}.py` mixins or sub-services. Preserve public method names via re-export or delegation shim.
- **Effort**: 🟡 **MEDIUM** — Lower risk than other splits. ~4-5 hours.
- **Confidence for one-shot fix**: 75% — mixin approach is safe; only 6 assertions need updating.

#### ARCH-8 — `_open_config_file` extraction blocker (source-string tests)
- **Severity**: Medium
- **Status**: Pending
- **Description**: `VoiceTyperApp._open_config_file` (104 LOC) is the only remaining "fat" method on `VoiceTyperApp`. Extraction blocked by 6 `inspect.getsource` tests in `tests/test_b4_config_editor_lock.py` and `tests/regressions/concurrency_test.py` that pin literal source text.
- **Recommended fix**: Port these 6 source-string tests to behavioral tests (RW-8 pattern), then extract `ConfigEditorLauncher`. ~1-day effort.
- **Effort**: 🟡 **MEDIUM** — The source-string porting is the tricky part. Must carefully preserve test behavior. The `_open_config_file` method is only 104 LOC and relatively self-contained. ~1 day.
- **Confidence for one-shot fix**: 80% — self-contained but source-string tests add friction.

#### ARCH-9 — `app.py` test-seam re-exports (173 monkeypatch sites)
- **Severity**: Low
- **Status**: Pending
- **Description**: `app.py` re-exports 20 symbols from sibling modules so tests can monkeypatch `voice_typer.server.app.X`. 173 monkeypatch sites depend on these re-exports.
- **Recommended fix**: Migrate monkeypatch sites to canonical paths (`voice_typer.server.server_platform.is_autostart_enabled` instead of `voice_typer.server.app.is_autostart_enabled`), then delete re-export blocks. Mechanical refactor touching many files.
- **Effort**: 🔴 **HIGH** — 72+ import sites across 65+ files, ~20 re-exported symbols. Every monkeypatch site must be migrated one-by-one. High risk of breaking tests. Cannot do in one shot confidently. ~1 day.
- **Confidence for one-shot fix**: 50% — wide surface area, many tests.

#### ARCH-10 — Circular import between `ipc_server.py` and `handlers/*.py`
- **Severity**: Low
- **Status**: Won't Fix
- **Description**: 13 handler mixins import `log` and `_validate_dict_payload` from `ipc_server.py`; `ipc_server.py` imports the mixins back. Cycle is broken by ordering (helpers defined before handler imports).
- **Rationale for Won't Fix**: Pattern is stable and documented. Moving helpers to `ipc_helpers.py` would be cleaner but provides no runtime benefit.
- **Effort**: None — Won't Fix by design.

#### ARCH-11 — `clipboard.py` (1,477 lines): UIA focus/pwd detection tangled with clipboard I/O
- **Severity**: Low
- **Status**: Pending
- **Description**: ~326 LOC of Win32 UI Automation focus/password-field detection at lines 440-765 mixed with clipboard I/O helpers.
- **Investigation**: VERIFIED. Functions to extract: `_is_elevated_target`, `_CRED_DIALOG_CLASSES`, `_focused_window_is_credential_dialog`, `_is_password_field`, `_UIA_SINGLETON/_UIA_MODULE/_UIA_SINGLETON_INIT_ATTEMPTED`, `_get_uia_singleton`, `_get_uia_focused_element`, `_is_content_editable`.
- **Risk discovered**: Tests mutate `clip_mod._UIA_SINGLETON` directly. After extraction, re-exports create module attributes that are independently mutable but changes won't propagate to `clipboard_target_safety`'s globals. Need to update ~4 test sites.
- **Recommended fix**: Extract to `voice_typer/server/clipboard_target_safety.py`. Update ~4 test sites that mutate `_UIA_SINGLETON` directly.
- **Effort**: 🟢 **LOW** — Self-contained extraction, ~4 test sites to update. ~2.5 hours.
- **Confidence for one-shot fix**: 80% — well-understood scope, low risk.

#### ARCH-12 — 164 `inspect.getsource` source-string tests across the codebase
- **Severity**: Low
- **Status**: Pending (ongoing)
- **Description**: 164+ source-string tests pin implementation structure (variable names, call-site spellings, call counts) rather than behavior. Make refactoring expensive.
- **Recommended fix**: Adopt project rule — "no new `inspect.getsource` tests; port existing ones when touching the code they pin." Chip away over time.
- **Effort**: 🔴 **EXTRA HIGH** — 164+ calls across 30+ test files. Not a discrete task — it's a project-wide migration. Chip away individually when touching pinned code. Cannot be done in one shot.
- **Confidence for one-shot fix**: 20% — cannot complete in one shot.

#### ARCH-13 — TYPE_CHECKING back-references from controllers to `VoiceTyperApp`
- **Severity**: Low
- **Status**: Won't Fix
- **Description**: 13 modules use `if TYPE_CHECKING:` to import `VoiceTyperApp` for type annotations. `VoiceTyperApp` IS the service locator.
- **Rationale for Won't Fix**: Runtime cycle is already broken via lazy imports. Annotating against `AppProtocol` (already defined in `providers.py`) would be cleaner but provides no runtime benefit.
- **Effort**: None — Won't Fix by design.

#### ARCH-15 — `service.py` (2,335 LOC): 50-method god facade spanning 8 domains
- **Severity**: Medium
- **Status**: Pending
- **Description**: `VoiceTyperService` exposes ~50 delegating methods grouped across 8 domains (history, model, onboarding, microphone_test, vocabulary, template, status, dictation). Class is currently a facade, so cost is readability/maintainability, not runtime coupling.
- **Root cause**: New service domains were added as methods on the single facade over time without extracting per-domain modules.
- **Recommended fix**: Split into `voice_typer/server/service/{history,model,onboarding,microphone_test,vocabulary,template,status,dictation}.py` mixins or sub-services. Preserve the public method names (tests + IPC handlers call them) via re-export or delegation shim. ~2-day effort.
- **Effort**: 🔴 **HIGH** — Same as ARCH-5 (duplicate item). ~2 days.

#### ARCH-18 — `ipc_server.py` `_handle_*` methods still inline
- **Severity**: Medium
- **Status**: **CLOSED (Outdated)**
- **Description**: Original review claimed `_handle_*` methods remained inline. Investigation found 67 of 70 handlers are already extracted to 13 mixin modules under `voice_typer/server/handlers/`.
- **Investigation**: Only 3 inline handlers remain (`_handle_heartbeat`, `_handle_relaunch_ack`, `_handle_unknown_command`) and are **intentionally resident** per inline comment at `ipc_server.py:1873-1881`.
- **No fix needed.**

---

#### ARCH-5 — `service.py` (2,096 lines): 70-method facade
- **Severity**: Medium
- **Status**: Pending
- **Description**: Duplicate of Architecture ARCH-5/ARCH-15 above.
- **Effort**: 🔴 **HIGH** — See Architecture ARCH-5.

#### ARCH-10 — Circular import between `ipc_server.py` and `handlers/*.py`
- **Severity**: Low
- **Status**: Won't Fix
- **Description**: Duplicate of Architecture ARCH-10 above.

---

> **Resolved & removed:** PERF-2, PERF-3, PERF-5, PERF-10, PERF-15 were all verified implemented in the codebase (`event_bus.py` RT-thread defer, `waveform_bubble_wiring.py` coalescing, `history_db.py` `Queue(maxsize=10000)`, `service.py` 5s TTL cache, legitimate `getattr` pattern) and removed from this list.



## Cross-Platform (all Pending)

#### XPLAT-11 — Linux aarch64 native listener not built by CI
- **Severity**: Medium
- **Status**: Pending (ADR deferral)
- **Description**: `.github/workflows/tauri-linux-build.yml` only builds `linux-key-listener` for x86_64. `tauri.conf.json` lists it as required for ALL platforms.
- **Recommended fix**: Per-arch resource list, OR generate stub, OR document manual `compile_native.sh` requirement.
- **Note**: ADR-0020 explicitly defers aarch64 Linux to a follow-up.
- **Effort**: 🔴 **HIGH** — Requires CI workflow changes, cross-compilation setup, and validation on real aarch64 Linux hardware (not available in sandbox). Cannot complete in one shot.
- **Confidence for one-shot fix**: 30% — blocked by hardware availability.

#### XPLAT-12 — Windows-on-ARM scaffolded but unvalidated
- **Severity**: Low
- **Status**: Pending (host validation required)
- **Description**: Code path is complete but `windows-11-arm` runner not yet GHA-available.
- **Note**: Per ADR §4.1, explicit deferral.
- **Effort**: 🔴 **HIGH** — Requires Windows-on-ARM runner access not available in this sandbox. Cannot complete.
- **Confidence for one-shot fix**: 10% — blocked by runner availability.

#### XPLAT-19 — [Partial] ADR §6.3 Win32 focus-restore now compiles
- **Severity**: High
- **Status**: **Partial** (compiles but not runtime-validated)
- **Description**: The Win32 focus-restore path (`src-tauri/src/commands/sidecar_cmds.rs`) now compiles (verified via `cargo check` EXIT:0 on win32 GNU target). Remaining work: real Windows host smoke test.
- **Recommended fix**: Run the `VALIDATE-ON-WINDOWS-HOST` block — launch elevated Notepad, dictate, confirm focus returns. Cannot be run in this sandbox.
- **Effort**: 🔴 **HIGH** — Requires actual Windows host with elevated Notepad. Cannot complete in sandbox. ~0.5 day on real hardware.
- **Confidence for one-shot fix**: 40% — blocked by hardware access.

---

## Test Infrastructure (all Pending)

#### TEST-2 — 99 `time.sleep` calls across 28 test files (flakiness-prone)
- **Severity**: Medium
- **Status**: Pending
- **Description**: 127+ `time.sleep(...)` calls across 28+ test files act as fixed-delay synchronization, which is flaky on loaded CI runners.
- **Root cause**: Tests synchronize on time instead of condition/event.
- **Recommended fix**: Replace fixed sleeps with condition waits (events, `threading.Event.wait`, or polling predicates). Chip away file-by-file. ~2-day effort.
- **Effort**: 🔴 **HIGH** — 127+ sleep calls across 28+ files. Each one needs individual analysis to determine the correct replacement (event.wait, polling predicate, etc.). ~2 days.
- **Confidence for one-shot fix**: 30% — cannot do all in one shot; chip away file-by-file.

#### TEST-3 — 164 `inspect.getsource` source-inspection tests (brittle)
- **Severity**: Low
- **Status**: Pending
- **Description**: 164+ tests pin implementation structure via `inspect.getsource`. Overlaps ARCH-12.
- **Recommended fix**: Adopt rule "no new `inspect.getsource` tests; port existing when touching pinned code."
- **Effort**: 🔴 **EXTRA HIGH** — Same as ARCH-12. Cannot be done in one shot.
- **Confidence for one-shot fix**: 20% — project-wide migration.

#### TEST-4 — `test_server.py` (2,799 LOC) + `test_app.py` (2,484 LOC) are spaghetti test files
- **Severity**: Low
- **Status**: Pending
- **Description**: The two largest test files bundle many unrelated test classes with shared heavy fixtures.
- **Recommended fix**: Split by domain into `tests/server/` submodules; share fixtures via `conftest.py`.
- **Effort**: 🔴 **HIGH** — Requires careful separation of test classes, extraction of shared fixtures, and ensuring no breakage. ~1-2 days.
- **Confidence for one-shot fix**: 50% — large files with many dependencies.

#### TEST-5 — 12 modules >650 LOC with no dedicated test file
- **Severity**: Low
- **Status**: Pending
- **Description**: 12 source modules over 650 LOC have no matching `tests/*` file.
- **Recommended fix**: Add focused unit-test files per module.
- **Effort**: 🔴 **EXTRA HIGH** — Adding comprehensive tests for 12 large modules is a major effort. Cannot be done in one shot.
- **Confidence for one-shot fix**: 20% — too many modules to cover in one shot.

---

## CI/CD (all Pending)

#### CI-1 — 5 `if: false` guards across 3 Tauri workflows (intentional, pre-Phase-0)
- **Severity**: Low
- **Status**: Pending (by design)
- **Description**: Five `if: false` guards disable jobs across 3 Tauri workflows; intentional scaffolding.
- **Recommended fix**: Remove guards progressively as each MIG phase lands.
- **Effort**: 🟢 **LOW** — Just removing `if: false` guards when the corresponding phase is ready. But cannot do until phases are validated. ~5 min per guard.
- **Confidence for one-shot fix**: 90% — simple YAML edits, but blocked on phase validation.

#### CI-2 — Windows workflow x86_64-only (no aarch64 Windows-on-ARM)
- **Severity**: Low
- **Status**: Pending
- **Description**: The Windows CI workflow builds only x86_64; Windows-on-ARM has no build/validate job.
- **Recommended fix**: Add an aarch64 Windows job once a runner is available.
- **Effort**: 🔴 **HIGH** — Blocked by runner availability. Cannot complete.
- **Confidence for one-shot fix**: 10% — blocked by GHA runner availability.

#### CI-4 — macOS signing order wrong (`.app` not signed before notarization)
- **Severity**: Medium
- **Status**: Pending
- **Description**: The macOS workflow invokes notarization before the `.app` bundle is signed.
- **Recommended fix**: Sign the `.app` first, then submit to notarytool.
- **Effort**: 🟡 **MEDIUM** — Requires reordering CI steps in `tauri-macos-build.yml`. Cannot validate without a real macOS runner. ~0.5 day.
- **Confidence for one-shot fix**: 60% — cannot verify without macOS runner.

---

## Dependencies (all Pending)

#### DEP-2 — `torch` undeclared but imported in 6+ source files
- **Severity**: Medium
- **Status**: Pending
- **Description**: `torch` is imported across 6+ source files but is not declared in the project's dependency manifest.
- **Recommended fix**: Add `torch` (pinned) to the dependency manifest, or gate the imports behind an optional extra.
- **Effort**: 🟡 **MEDIUM** — Need to identify which imports are truly required vs optional, decide on pinning strategy, update `pyproject.toml` and lockfile. ~0.5 day.
- **Confidence for one-shot fix**: 85% — straightforward dep addition.

---

## Accessibility (A11Y-6 resolved 2026-07-19)

#### A11Y-6 — Settings tabs use `radiogroup` pattern, not `tablist`
- **Resolved & removed** (2026-07-19): `components/ui/segmented-control.tsx` tabs variant now renders `role="tablist"` with `role="tab"` + `aria-selected` + roving `tabIndex` and arrow-key nav (`role={isTabs ? "tablist" : "radiogroup"}`). `pages/Settings.tsx` uses `variant="tabs"`, so the Settings tabs get correct `tablist` semantics.

---


## MIG-1.1–1.9 — Desktop Runtime Migration

> Migration spec: `docs/adr/0020-desktop-runtime-migration-analysis.md`.

- **MIG-1.1 (Boot + sidecar spawn)**: PARTIAL. `src-tauri` Rust host compiles clean on win32 GNU target.
- **MIG-1.2 (IPC bridge)**: PARTIAL. 190 IPC tests pass.
- **MIG-1.3 (Config mirror)**: PARTIAL. Config read/write scaffolded.
- **MIG-1.4 (Tray + windowing)**: PARTIAL. Tray commands compiled.
- **MIG-1.5–1.9 (Real Host Validation)**: **NOT IMPLEMENTED (Partial)**.
- **Effort**: 🔴 **EXTRA HIGH** — Requires real Windows + macOS + Linux Wayland + Linux aarch64 hosts with real Nuitka builds, code-signing, and behavioral smoke tests. Cannot be done in this sandbox.
- **Confidence for one-shot fix**: 10% — entirely blocked by hardware/lab access.

---

**Bottom line for the next agent:** Do NOT trust "all green on Linux" as proof of cross-platform cutover.
