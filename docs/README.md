# Voice Typer Documentation Index

This is a navigational index of the docs/ tree. Files are grouped by
intended audience so you can jump straight to the section that matters
for your role. The repo root also has top-level docs that apply across
audiences: [`README.md`](../README.md), [`CONTRIBUTING.md`](../CONTRIBUTING.md),
[`SECURITY.md`](../SECURITY.md), [`RELEASING.md`](../RELEASING.md),
[`FEATURES.md`](../FEATURES.md), [`AGENTS.md`](../AGENTS.md).

## For end users

| File | Purpose |
|------|---------|
| [home-directory.md](home-directory.md) | Where Voice Typer stores config, models, history, logs (per-OS paths). |
| [duplicated-text.md](duplicated-text.md) | Why dictation sometimes produces doubled text and how to avoid it. |

## For contributors

| File | Purpose |
|------|---------|
| [ARCHITECTURE.md](ARCHITECTURE.md) | High-level architecture: renderer ↔ Electron main ↔ Python backend ↔ Rust host. Start here. |
| [python-api.md](python-api.md) | Python class API reference (`VoiceTyperApp`, `Recorder`, `TranscriptionEngine`, `Config`, etc.). Kept in sync with the code by `tests/test_api_doc_accuracy.py`. |
| [ipc-reference.md](ipc-reference.md) | IPC message reference — 69 commands (67 renderer-reachable + 2 host-only) + 36 push events grouped by namespace, with the four-allowlist contract (server registry + Electron allowlist + renderer types + Rust host) and per-command notes. |
| [API.md](API.md) | **Deprecated.** Retained only for inbound-link compat — see `python-api.md` + `ipc-reference.md` instead. |
| [debugging.md](debugging.md) | How to read the logs, attach a debugger, and reproduce common failure modes. |
| [ruff-ratchet.md](ruff-ratchet.md) | How the ruff lint ratchet works (`ruff-baseline.json` + `scripts/ruff_ratchet_check.py`). |
| [native-hotkey-architecture-plan.md](native-hotkey-architecture-plan.md) | **Historical plan** for the cross-platform native-hotkey rewrite. See ADR-0007 for what shipped. |
| [auto-update-feature.md](auto-update-feature.md) | Design spec for auto-update (NOT IMPLEMENTED — design-only). |
| [modules/](modules/) | Per-module deep dives: [_index.md](modules/_index.md), [audio_quality_controller.md](modules/audio_quality_controller.md), [sidecar_ws.md](modules/sidecar_ws.md), [shutdown_controller.md](modules/shutdown_controller.md). |
| [architecture/](architecture/) | Cross-cutting architecture contracts: [error-envelope-contract.md](architecture/error-envelope-contract.md). |
| [ux/](ux/) | UX rationale notes: [model-delete-rationale.md](ux/model-delete-rationale.md). |

## For release engineers / migration drivers

| File | Purpose |
|------|---------|
| [PLATFORM_STATUS.md](PLATFORM_STATUS.md) | Per-platform support matrix (OS versions, arches, known-good combos). |
| [migration/tauri-sidecar-bridge.md](migration/tauri-sidecar-bridge.md) | The Tauri ↔ Python sidecar WebSocket bridge architecture (ADR-0020 implementation). |
| [migration/tauri-build-runbook.md](migration/tauri-build-runbook.md) | How to run `cargo tauri build` per platform (display server + toolchain requirements). |
| [migration/windows-validation-runbook.md](migration/windows-validation-runbook.md) | 9-point Phase 0-W validation gate (Windows). |
| [migration/macos-validation-runbook.md](migration/macos-validation-runbook.md) | 9-point Phase 0-M validation gate (macOS, both arches). |
| [migration/linux-validation-runbook.md](migration/linux-validation-runbook.md) | 9-point Phase 0-L validation gate (Linux X11 + Wayland, both arches). |
| [migration/signing-guide.md](migration/signing-guide.md) | Windows Authenticode + macOS Developer ID/notarization + Linux signing story. |
| [migration/cutover-playbook.md](migration/cutover-playbook.md) | Per-platform cutover criteria + rollback procedure for the Electron → Tauri migration. |

## For security reviewers

| File | Purpose |
|------|---------|
| [security/credential-store.md](security/credential-store.md) | How API keys are stored per-OS (Keychain / Credential Manager / Secret Service). |
| [privacy/encryption-at-rest.md](privacy/encryption-at-rest.md) | What user data is encrypted at rest and how. |
| [privacy/gdpr-export.md](privacy/gdpr-export.md) | GDPR data-export bundle layout + contents. |
| [privacy/gdpr-delete.md](privacy/gdpr-delete.md) | GDPR delete-all behavior — what is removed and what survives. |
| [adr/0006-clipboard-security.md](adr/0006-clipboard-security.md) | Clipboard borrow/restore threat model. |
| [adr/0014-tcp-ipc-session-token-auth.md](adr/0014-tcp-ipc-session-token-auth.md) | TCP IPC bearer-token auth (SEC-018). |
| [adr/0017-cloud-url-allowlist-https.md](adr/0017-cloud-url-allowlist-https.md) | Cloud ASR / LLM URL allowlist (RELIABILITY-004). |

## Architecture Decision Records (ADRs)

The full ADR index lives at [adr/README.md](adr/README.md). ADRs are
numbered `0000`..`0020` and document irreversible design decisions
(hotkey backend, IPC protocol, sandbox boundaries, runtime migration,
etc.). Read [adr/0000-adr-process.md](adr/0000-adr-process.md) for the
process and [adr/template.md](adr/template.md) when adding a new ADR.

## Historical documents

The following historical planning + progress trackers live at the top
level of `docs/` (there is no `docs/history/` subdirectory — they are
sibling files). They are retained for design rationale; they are NOT
authoritative for current state. Each file carries a STATUS header at
the top noting whether its action items are still live.

| File | Purpose |
|------|---------|
| [rw04-recording-decomposition.md](rw04-recording-decomposition.md) | recording-lifecycle decomposition notes (RecordingController extraction). |
| [rw8-meta-tests-triage.md](rw8-meta-tests-triage.md) | triage of the now-deleted `tests/test_bugfix_regressions.py` meta-tests. Historical; the source file has been removed and the surviving behavioral ports live in `tests/test_bugfix_regressions_behavioral.py`. |
| [rw9-god-class-decomposition.md](rw9-god-class-decomposition.md) | `VoiceTyperApp` god-class decomposition tracker (controller extractions, `app.py` line-count history). |
| [vitest-rewrite-progress.md](vitest-rewrite-progress.md) | vitest rewrite progress tracker for the 87 string-pattern Python tests. Historical; `tests/test_feature_hardening_regressions.py` was deleted post-rewrite. |
| [native-hotkey-architecture-plan.md](native-hotkey-architecture-plan.md) | Historical plan for the cross-platform native-hotkey rewrite. See ADR-0007 for what shipped. |
