# Cutover Playbook — Electron → Tauri (ADR-0020 Phase 5)

**Status**: this is the **per-platform cutover procedure** for flipping the
default shipping Voice Typer app from Electron to Tauri. It is the
authoritative playbook for Phase 5 of ADR-0020. Cutover is **per-platform**,
not all-at-once: Windows first, then macOS, then Linux. The Electron build
path stays intact and shippable on every platform throughout — Tauri is
strictly additive until the platform's cutover gate is met.

**Scope of this document**:
- Cutover criteria per platform (what evidence is required to flip).
- Cutover procedure (exact steps: which CI to enable, which electron-builder
  target to disable, how to update release notes).
- Rollback procedure (how to revert per-platform).
- Per-platform cutover order.
- Mixed-mode period (some users on Electron, some on Tauri) support handling.

**Out of scope**:
- Per-platform build steps — see `tauri-build-runbook.md` + per-platform
  runbooks.
- Signing — see `signing-guide.md`.
- Auto-update — out of scope for v1 (ADR-0020 §15). Users upgrade by
  downloading the new release manually, same as today.

---

## Cutover criteria per platform

A platform may be cut over (its default shipping app flipped from Electron
to Tauri) ONLY when **all** of the following are true on a real host for
that platform's target arch(s):

### Hard criteria (all must pass)

1. **Phase 0 spike passes** on a real host for that platform (see the
   per-platform runbook for the 9-point gate). The spike must be run on
   the **minimum** supported OS for that platform (Win10 22H2, macOS 13,
   Ubuntu 22.04) AND on a recent release (Win11, macOS 14, Fedora 40).
2. **All 9 Phase 0 validation points pass** on a real host:
   - Nuitka sidecar exe builds from `python-build-standalone`.
   - `externalBin` sidecar spawns via Tauri; Rust reads `server_started`
     JSON from stdout and connects the WS.
   - HMAC handshake: wrong token rejected; correct token accepted.
   - `faster-whisper` `WhisperModel("tiny")` loads + transcribes inside
     the Nuitka exe (proves CTranslate2/DLLs/models).
   - `enigo` types text into a foreground window (Notepad / TextEdit /
     `gnome-text-editor`); clipboard+Ctrl+V path verified (required on
     Wayland where `enigo.text()` is expected to fail).
   - `tauri-plugin-notification` posts a native toast.
   - Cooperative `{"type":"shutdown"}` exits cleanly; `kill_children`
     cleans on hard kill.
   - Prewarm still warms the cache (per the platform's prewarm scheduler —
     Task Scheduler / LaunchAgent / systemd user timer).
   - Native hotkey binary still toggles dictation.
3. **FT-1 crash isolation** verified: kill the sidecar process from
   Task Manager / Activity Monitor / `kill -9` — the UI shows the
   "reconnecting…" state, the Rust supervisor respawns the sidecar, and
   dictation resumes within the backoff window. After repeated kills
   (5+), the host falls back to full-app relaunch via `AppHandle::restart()`.
4. **No regressions** vs the Electron path on a side-by-side smoke test:
   - Tray menu opens + all items work.
   - Settings persist across restarts.
   - History (SQLite WAL) round-trips.
   - Vocabulary / templates / automation persist.
   - Audio filter chain (ADR-0009) loads.
   - VAD (silero) loads.
   - Cloud engines (if configured) work.
   - Clipboard snapshot/restore (ADR-0006/0012) works on the long-text
     paste path.
5. **Single-instance** verified: a second launch focuses the first window
   (Tauri `single-instance` plugin) on every platform.
6. **Bundle size** within tolerance: Tauri installer ≤ Electron installer
   + 50 MB (the sidecar is the same size; the savings is Chromium).
7. **Startup latency** within tolerance: cold start ≤ Electron cold start
   + 2 s (sidecar cold start is 2–5 s; prewarm mitigates this).
8. **Signing + notarization** verified end-to-end (see `signing-guide.md`):
   - Windows: MSI + NSIS + sidecar exe Authenticode-signed; SmartScreen
     does not flag (requires EV cert for immediate reputation; OV cert
     requires reputation build-up).
   - macOS: `.app` + `.dmg` Developer ID-signed, notarized, stapled;
     `spctl --assess --verbose` passes; `xcrun stapler validate` passes.
   - Linux: `.deb` + `.rpm` install cleanly with the existing
     `postinst`/`prerm` scripts; udev rule installed; user added to
     `input` group (or prompted at next login).
9. **User acceptance test** signed off by a real user (not the implementer)
   on each target arch for that platform. The user must complete a
   full dictation session: launch → grant mic permission → toggle via
   global hotkey → dictate → see text injected → stop → quit → relaunch
   → verify history persisted.

### Soft criteria (warn but do not block)

- macOS `pyobjc` + hardened runtime + notarization friction (ADR-0020
  Risk #5): if Phase 0-M passes, this is de-risked; otherwise defer.
- Linux aarch64 (ADR-0020 Risk #7): if `python-build-standalone` aarch64
  Linux + CTranslate2 aarch64 wheels + glibc pinning prove unstable,
  defer aarch64 Linux to a follow-up. x86_64 Linux can cut over
  independently.
- WebView CSS/JS differences (ADR-0020 Risk #8): if Phase 3 (UI port)
  audit reveals webkit2gtk-only CSS regressions, defer Linux cutover
  until the audit is complete. Windows (WebView2) and macOS (WKWebView)
  are not affected.

### Evidence trail (must be filed before flip)

For each platform that flips, file the following in the release notes
for that version:

- [ ] Per-platform runbook checklist (all 9 points) — checked + dated.
- [ ] FT-1 crash isolation test — log excerpt showing `ft1_relaunching`
      events + successful respawn.
- [ ] Side-by-side smoke test — screenshot or video.
- [ ] Bundle size + startup latency measurements (with comparison to
      the prior Electron release).
- [ ] Signing verification: `signtool verify` (Win), `spctl --assess`
      (macOS), `dpkg -I` / `rpm -qpi` (Linux).
- [ ] User acceptance sign-off (name + date + target arch + OS version).
- [ ] Rollback plan confirmed: the prior Electron installer is still
      downloadable from the same release page.

---

## Cutover procedure (per platform)

> **One platform at a time.** Do NOT cut over multiple platforms in the
> same release. Each platform's cutover is its own release.

### Step 1 — Pre-flight (T-1 release)

- Confirm all hard criteria above are met + the evidence trail is filed.
- Tag a release candidate: `v<version>-rc.<platform>` (e.g.
  `v1.2.0-rc.windows`).
- Run the top-level CI workflow manually:
  `Actions → Tauri Build (all platforms) → Run workflow → platform: <platform>`.
  Confirm the per-platform workflow produces a signed installer artifact.
- Have a rollback pilot user (NOT the implementer) install the RC + run
  the user acceptance test.

### Step 2 — Flip the default (T-0 release)

For the platform being cut over, in the same release tag (`v<version>`):

1. **Enable the per-platform Tauri workflow's top-level `if:` guard.**
   - File: `.github/workflows/tauri-<platform>-build.yml` (owned by
     sub-agents #5/#6/#7).
   - Change `if: false` → `if: true` (or remove the guard).
   - This makes the per-platform workflow run on tag push automatically.
2. **Disable the electron-builder target for that platform.**
   - File: `voice_typer/client/electron-builder.yml`.
   - Comment out the platform's `target:` entries (e.g., on Windows
     cutover, comment out the `win:` section's `target: [nsis]`).
   - The Electron build PATH stays in the repo (reversible fallback) —
     only the active target is disabled.
3. **Update the release notes** for `v<version>` with:
   - "Default shipping app on `<platform>` is now Tauri v2."
   - Link to the evidence trail filed in Step 1.
   - Link to the prior Electron release (rollback path).
   - "macOS / Linux users: no change this release" (if only Windows
     cut over).
4. **Tag + push.** The CI builds the Tauri installer for the cut-over
   platform + the Electron installer for the not-yet-cut-over platforms.
   Both are uploaded to the same release.

### Step 3 — Post-flip monitoring (T+1 to T+14 days)

- Monitor the GitHub issue tracker for `<platform>`-specific
  regressions.
- Monitor the auto-reported crash logs (if the user opts in — no PII).
- After 14 days with no critical regressions, the platform is considered
  "stable on Tauri" and the Electron fallback can be marked "legacy" in
  the release notes (but NOT deleted from the repo).

---

## Rollback procedure (per platform)

> **Rollback is per-platform.** Rolling back Windows does NOT roll back
> macOS or Linux. The Electron code path stays intact on every platform
> until that platform has been stable on Tauri for ≥ 1 release cycle.

### To roll back a platform that was just cut over:

1. **Re-enable the electron-builder target for that platform.**
   - File: `voice_typer/client/electron-builder.yml`.
   - Uncomment the platform's `target:` entries commented out in Step 2.2.
2. **Disable the per-platform Tauri workflow's top-level `if:` guard.**
   - File: `.github/workflows/tauri-<platform>-build.yml`.
   - Change `if: true` → `if: false` (or restore the Phase-0-gate guard).
3. **Tag a hotfix release** (`v<version>.<patch>`) with:
   - "Rolling back `<platform>` to Electron due to <issue link>."
   - "Tauri build for `<platform>` is still downloadable from this
     release as a beta — user feedback wanted."
4. The CI now builds the Electron installer for the rolled-back platform
   again. The Tauri installer can still be built manually via the
   `workflow_dispatch` orchestrator for users who want to opt in.

### What does NOT change on rollback:

- No data, config, or model loss. The Tauri build writes to the same
  OS-specific data dir as the Electron build (`<config_dir>/voice-typer/`).
- The user's history DB, vocabulary, templates, automation, models, and
  settings all carry over in both directions (Electron→Tauri→Electron).
- The Python sidecar is the same binary in both paths (Nuitka-compiled
  from the same `voice_typer/server/` tree).

---

## Per-platform cutover order

Per ADR-0020 §"Migration Plan" + §"Phase 5 — Validation & cutover":

| Order | Platform | Why this order | Arch(s) | Phase 0 gate |
|-------|----------|----------------|---------|--------------|
| 1st | Windows | Largest user base; smallest Tauri unknowns (WebView2 = Chromium, no notarization, no Wayland). | x86_64 first; aarch64 follows. | Phase 0-W (9 points) |
| 2nd | macOS | Apple Silicon + Intel; notarization is the highest-risk unknown (ADR-0020 Risk #5). | aarch64 + x86_64 (two DMGs). | Phase 0-M |
| 3rd | Linux | X11 first (mature), then Wayland (clipboard+Ctrl+V fallback for `enigo`); aarch64 may defer. | x86_64 X11 → x86_64 Wayland → aarch64. | Phase 0-L |

**Each platform is independent.** Windows can ship Tauri while macOS still
ships Electron, and the two are independently revertible. There is no
"all-platforms cut over" milestone — the migration is complete when each
platform has been stable on Tauri for ≥ 1 release cycle.

### Linux sub-order (X11 before Wayland, x86_64 before aarch64)

- **X11 before Wayland**: `enigo.text()` works on X11. On Wayland, the
  clipboard+Ctrl+V fallback replaces the user's clipboard temporarily
  (mitigated by `clipboard_snapshot.py` borrow/restore). Cut over X11
  first; Wayland users stay on Electron until the Wayland UX is
  validated as acceptable.
- **x86_64 before aarch64**: aarch64 Linux is less tested
  (`python-build-standalone` aarch64 + CTranslate2 aarch64 wheels +
  glibc pinning — ADR-0020 Risk #7). Defer aarch64 Linux to a follow-up
  if Phase 0-L on x86_64 passes but aarch64 is unstable.

---

## Mixed-mode period

During the transition, **some users are on Electron and some on Tauri**
for the same platform. This is expected and supported. Both builds read
+ write the same data dir, so users can switch between them freely.

### How to tell which build a user is on

> **Note (DOC-2):** the `runtime=tauri` / `runtime=electron` first-log-line
> marker is a **planned future feature** — it is referenced by
> `tests/tauri/mig19/test_linux_cutover.py` as the intended cutover
> verification mechanism, but no code in `src-tauri/src/` or
> `voice_typer/server/` currently writes that line yet. Until it is
> implemented, identify the runtime via the process-name heuristics
> below (Windows Task Manager / macOS Activity Monitor / Linux `ps`).

**Planned log marker** (not yet emitted by either runtime):

```
runtime=tauri version=1.2.0 target=x86_64-pc-windows-msvc rustc=1.77.x
```

or

```
runtime=electron version=1.2.0 electron=28.x.x node=20.x.x
```

When implemented, support tickets MUST include this line. If the user
cannot find it (or until the marker is implemented), the build can be
identified by:
- **Windows**: Task Manager shows `voice-typer-tauri.exe` (Tauri) vs
  `Voice Typer.exe` (Electron). Tauri also spawns `python-sidecar-*.exe`;
  Electron spawns `python.exe`.
- **macOS**: Activity Monitor shows `voice-typer-tauri` (Tauri) vs
  `Voice Typer` (Electron). The `.app` bundle name is the same, so use
  the process name.
- **Linux**: `ps aux | grep voice-typer` shows `voice-typer-tauri`
  (Tauri) vs `voice-typer` (Electron).

### Support ticket triage

1. Identify the user's runtime (`runtime=tauri` vs `runtime=electron`
   from the log first line once the marker is implemented; otherwise
   via the process-name heuristics above).
2. If the user is on Electron: handle as a normal Electron support
   ticket. Do NOT suggest they switch to Tauri unless their issue is
   "the app is too heavy" or "Chromium is conflicting with X".
3. If the user is on Tauri:
   - Check whether the issue reproduces on Electron (have the user
     download the Electron installer from the same release page).
   - If YES on both → it's a Python-sidecar issue (not a Tauri issue);
     file under the appropriate backend component.
   - If NO on Electron only → it's a Tauri-specific regression; file
     under `tauri-host` and consider rollback per §"Rollback procedure".
4. If the user is on a **beta Tauri build** (downloaded manually from
   the release page on a platform that hasn't cut over yet), make this
   clear in the ticket — beta builds are not supported with the same
   SLA as the default shipping app.

### Release notes language during mixed-mode

Every release during the transition MUST clearly state, per platform:
- "Default shipping app: Electron" or "Default shipping app: Tauri".
- "Alternative build available: Tauri (beta)" or
  "Alternative build available: Electron (legacy fallback)".
- A link to both installers on the release page.

This prevents user confusion when a user on Tauri sees a release note
about an Electron-specific fix (or vice versa).

---

## Cutover log

Track every cutover (and rollback) in this section. Format:

```
- YYYY-MM-DD  v<version>  <platform>  CUT OVER  evidence: <link>  sign-off: <name>
- YYYY-MM-DD  v<version>  <platform>  ROLLED BACK  reason: <issue link>
```

(Empty until the first platform cuts over.)

---

## See also

- [`tauri-build-runbook.md`](./tauri-build-runbook.md) — master index for
  the Tauri build pipeline (cross-cutting) + per-platform runbook links.
- [`signing-guide.md`](./signing-guide.md) — Windows Authenticode, macOS
  Developer ID + notarization + stapling, Linux unsigned + the
  no-auto-update audit (ADR-0020 §13 + §15).
- [`tauri-sidecar-bridge.md`](./tauri-sidecar-bridge.md) — the 78-command
  + 24-event wire contract (ADR-0020 §2 + §"Sidecar→UI Event Table").
- [`../adr/0020-desktop-runtime-migration-analysis.md`](../adr/0020-desktop-runtime-migration-analysis.md)
  — the authoritative migration spec (Phase 5 + §"Reversibility").
