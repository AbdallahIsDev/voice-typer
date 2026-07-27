# Signing Guide — Tauri v2 Bundles (ADR-0020 §13 + §15)

**Status**: this is the **authoritative code-signing + notarization guide**
for the Tauri v2 builds of Voice Typer, covering Windows (Authenticode),
macOS (Developer ID + notarization + stapling), and Linux (unsigned by
default). It also documents the **no-auto-update** decision (ADR-0020 §15)
and the audit results confirming `tauri-plugin-updater` is not wired.

**Scope**:
- Windows: Authenticode signing of the Nuitka sidecar exe + prewarm exe +
  MSI/NSIS installer. Reuses the existing `WIN_CSC_LINK` / `CSC_LINK`
  env vars from `voice_typer/client/electron-builder.yml`.
- macOS: Developer ID Application signing + `notarytool` notarization +
  `stapler` stapling of the `.app` + `.dmg`. Reuses the existing
  `MAC_SIGNING_IDENTITY` + `APPLE_ID` + `APPLE_TEAM_ID` env vars.
- Linux: unsigned by default (no certificate authority for Linux desktop
  apps). Optional GPG-sign for `.deb` / `.rpm` / AppImage is documented
  but out of scope for v1.

**Out of scope**:
- Per-platform build steps — see `tauri-build-runbook.md` + per-platform
  runbooks.
- Cutover procedure — see `cutover-playbook.md`.
- Auto-update — explicitly out of scope for v1 (ADR-0020 §15). See
  §"No auto-update (ADR-0020 §15)" below for the audit results.

---

## Reused signing identities (no cert duplication)

To avoid cert duplication in CI, the Tauri build reuses the same signing
identities + env vars as the existing Electron build. Source of truth:
`voice_typer/client/electron-builder.yml` + `.github/workflows/build.yml`.

| Platform | Env var(s) | Source in repo | Reuse for Tauri |
|----------|------------|----------------|-----------------|
| Windows | `WIN_CSC_LINK` / `CSC_LINK` | `electron-builder.yml` `win.signAndEditExecutable: true` + comment block | Pass to `signtool sign` for the sidecar + prewarm + MSI. |
| Windows | `WIN_CSC_KEY_PASSWORD` / `CSC_KEY_PASSWORD` | (cert password secret in GitHub) | Same. |
| macOS | `MAC_SIGNING_IDENTITY` | `electron-builder.yml` `mac.identity: ${env.MAC_SIGNING_IDENTITY}` | Pass to `codesign --sign` for the sidecar + `.app`. |
| macOS | `APPLE_ID`, `APPLE_PASSWORD`, `APPLE_TEAM_ID` | `.github/workflows/build.yml` (existing macOS job) | Pass to `xcrun notarytool submit`. |

> **CI secret rotation**: rotating any of these secrets for the Tauri
> build rotates them for the Electron build too (same secret name). This
> is intentional — one cert per platform, not one per runtime.

---

## Windows — Authenticode (ADR-0020 §13.1)

### Signing order

Per ADR-0020 §13.1:

1. **Sign the Nuitka sidecar exe** immediately after build, before it
   enters the Tauri bundle. Unsigned sidecars trigger SmartScreen / AV.
2. **Sign the prewarm exe** (same reason).
3. **Tauri builds the MSI/NSIS**; the Tauri bundler signs the main
   executable + installer using the same cert.
4. **(Optional) re-sign the final bundle.** Keep cert + timestamp server
   configured in CI.

### Cert requirements

| Cert type | SmartScreen behavior | Cost | Use |
|-----------|----------------------|------|-----|
| **OV (Organization Validated)** | Reputation build-up required: new certs get SmartScreen warning for the first ~1000 downloads. | ~$200/yr | Acceptable for established apps with steady download volume. |
| **EV (Extended Validation)** | Immediate reputation: no SmartScreen warning from day one. Requires hardware token (USB HSM) or cloud HSM. | ~$400/yr | Recommended for the first Tauri release on Windows to avoid the SmartScreen reputation build-up period. |

> **Reuse the existing cert**: the `WIN_CSC_LINK` secret in CI is
> already configured for the Electron build. If it's an OV cert, the
> Tauri build inherits the reputation build-up burden (the cert's
> reputation is per-cert, not per-binary, so the same cert on the
> Tauri exe benefits from the Electron exe's prior reputation). If
> it's an EV cert, no reputation build-up is needed.

### Signing command (sidecar + prewarm exes)

```powershell
# Decompose the PFX (CSC_LINK is a path to a .pfx file; CSC_KEY_PASSWORD is its password).
$PFX = $env:WIN_CSC_LINK          # or $env:CSC_LINK (alias)
$PWD = $env:WIN_CSC_KEY_PASSWORD  # or $env:CSC_KEY_PASSWORD (alias)

# Sign the sidecar exe (built by scripts/build/build_sidecar_windows.sh).
signtool sign `
    /fd SHA256 `
    /tr http://timestamp.digicert.com `
    /td SHA256 `
    /f $PFX `
    /p $PWD `
    src-tauri\bin\python-sidecar-x86_64-pc-windows-msvc.exe

# Sign the prewarm exe (built by scripts/build/build_prewarm_windows.sh).
signtool sign `
    /fd SHA256 `
    /tr http://timestamp.digicert.com `
    /td SHA256 `
    /f $PFX `
    /p $PWD `
    src-tauri\resources\prewarm-x86_64-pc-windows-msvc.exe
```

For `aarch64-pc-windows-msvc` (Windows on ARM), repeat with the
`aarch64-pc-windows-msvc` target triple.

### Timestamp server

Use an RFC-3161 timestamp server (DigiCert shown above) so the signature
survives cert expiry. Alternative timestamp servers:

- `http://timestamp.digicert.com` (DigiCert — used above)
- `http://timestamp.sectigo.com` (Sectigo)
- `http://timestamp.globalsign.com/tsa/r6advanced1` (GlobalSign)

### Tauri bundler signing (MSI + NSIS)

The Tauri bundler signs the MSI + NSIS automatically if the
`TAURI_SIGNING_PRIVATE_KEY` + `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` env
vars are set. However, **for v1 we do NOT use the Tauri bundler's
updater signing** (per ADR-0020 §15 — no auto-update). Instead, the MSI
+ NSIS are signed with the same `signtool` command above:

```powershell
# After `cargo tauri build --target x86_64-pc-windows-msvc`:
signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 `
    /f $PFX /p $PWD `
    "src-tauri\target\x86_64-pc-windows-msvc\release\bundle\msi\Voice Typer_1.0.0_x64_en-US.msi"

signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 `
    /f $PFX /p $PWD `
    "src-tauri\target\x86_64-pc-windows-msvc\release\bundle\nsis\Voice Typer_1.0.0_x64-setup.exe"
```

### Verify

```powershell
signtool verify /pa /v "src-tauri\bin\python-sidecar-x86_64-pc-windows-msvc.exe"
signtool verify /pa /v "src-tauri\target\...\release\bundle\msi\Voice Typer_1.0.0_x64_en-US.msi"
```

Both must report `Successfully verified` with a count of `1` or more
cert chains.

### Nuitka `--onefile` self-extraction caveat (ADR-0020 §13.1)

Nuitka `--onefile` bundles an inner exe that extracts to a temp dir at
runtime. **Only the outer `.exe` is signed** — the extracted inner exe
is transient and not separately signed. AV may briefly flag the temp
extraction; this is expected and benign, NOT a packaging bug. Do not
attempt to sign the inner payload.

### Antivirus / SmartScreen QA note

During `--onefile` self-extraction, the inner exe briefly appears in a
temp dir *unsigned*; procmon / AV consoles will show an "unsigned" child
process. This is the expected transient stage, **not** a packaging bug —
do not flag it in QA. The outer `.exe` is what is Authenticode-signed
and what SmartScreen validates.

### Cross-reference

- Sub-agent #5's Windows validation runbook: `windows-validation-runbook.md`
  Step 1 (Nuitka build) + Step 8 (signing).
- `.github/workflows/build.yml` `build-windows` job: existing
  `WIN_CSC_LINK` / `CSC_LINK` env wiring.

---

## macOS — Developer ID + notarization + stapling (ADR-0020 §13.2)

### Signing order

Per ADR-0020 §13.2:

1. **Sign the Nuitka sidecar binary** with Developer ID Application
   immediately after build, before it enters the `.app` bundle.
2. **Sign the prewarm binary** (same).
3. **Add the binaries to the `.app` bundle's** `Contents/Resources/`
   (prewarm) or `Contents/MacOS/` (sidecar).
4. **Sign the entire `.app` bundle** with `--deep` (or, preferably,
   signed leaf-to-root manually).
5. **Notarize the `.app`** (`xcrun notarytool submit ... --wait`).
6. **Staple the `.app`** (`xcrun stapler staple <app>`).
7. **Build the DMG** from the stapled `.app`.
8. **Sign + notarize + staple the DMG** (same flow as the `.app`).

### Required `Info.plist` keys for the `.app` (ADR-0020 §13.2)

| Key | Value | Why |
|-----|-------|-----|
| `CFBundleIdentifier` | `com.voicetyper.app` | Matches today's `electron-builder.yml` `appId`. |
| `LSMinimumSystemVersion` | `13.0` | Matches `PLATFORM_STATUS.md` minimum. |
| `LSUIElement` | `false` | Main app shows in Dock. (Sidecar sets `LSUIElement=true` separately.) |
| `NSMicrophoneUsageDescription` | (required) | `sounddevice` mic access. |
| `NSUserNotificationsUsageDescription` | (required) | `tauri-plugin-notification` on macOS 11+. |

### Hardened runtime entitlements

| Entitlement | Why |
|-------------|-----|
| `com.apple.security.cs.allow-jit` | CTranslate2 may use JIT. |
| `com.apple.security.cs.disable-library-validation` | Nuitka `--onefile` extracts unsigned dylibs at runtime. Coordinate with Apple's notarization docs. |
| `com.apple.security.device.audio-input` | Mic access. |

### Signing command (sidecar + prewarm binaries)

```bash
# Reuse the existing identity (MAC_SIGNING_IDENTITY secret in CI).
# Format: "Developer ID Application: Your Name (XXXXXXXXXX)"
IDENTITY="$MAC_SIGNING_IDENTITY"

# Sign the sidecar binary (built by scripts/build/build_sidecar_macos.sh).
codesign --force --options runtime --sign "$IDENTITY" \
    --entitlements src-tauri/entitlements.plist \
    src-tauri/bin/python-sidecar-x86_64-apple-darwin

codesign --force --options runtime --sign "$IDENTITY" \
    --entitlements src-tauri/entitlements.plist \
    src-tauri/bin/python-sidecar-aarch64-apple-darwin

# Sign the prewarm binary (built by scripts/build/build_prewarm_macos.sh).
codesign --force --options runtime --sign "$IDENTITY" \
    src-tauri/resources/prewarm-x86_64-apple-darwin

codesign --force --options runtime --sign "$IDENTITY" \
    src-tauri/resources/prewarm-aarch64-apple-darwin
```

### Signing the `.app` bundle

```bash
# After `cargo tauri build --target <arch>-apple-darwin`:
APP="src-tauri/target/<arch>-apple-darwin/release/bundle/macos/Voice Typer.app"

# Sign the entire bundle (--deep walks the bundle and signs leaf-to-root).
codesign --deep --force --options runtime --sign "$IDENTITY" \
    --entitlements src-tauri/entitlements.plist \
    "$APP"

# Verify the signature.
codesign --verify --verbose=4 "$APP"
spctl --assess --verbose=4 "$APP"   # Gatekeeper assessment
```

> **Prefer leaf-to-root manual signing** over `--deep` for production
> releases — `--deep` is deprecated by Apple and doesn't handle all
> edge cases (e.g., embedded frameworks with their own entitlements).
> For v1, `--deep` is acceptable; track a follow-up to migrate to
> leaf-to-root.

### Notarize + staple the `.app`

```bash
# Submit the .app for notarization (zipped — notarytool requires a zip).
ditto -c -k --keepParent "$APP" /tmp/voice-typer-app.zip

xcrun notarytool submit /tmp/voice-typer-app.zip \
    --apple-id "$APPLE_ID" \
    --password "$APPLE_PASSWORD" \
    --team-id "$APPLE_TEAM_ID" \
    --wait

# Staple the ticket to the .app.
xcrun stapler staple "$APP"
xcrun stapler validate "$APP"
```

### Notarize + staple the `.dmg`

```bash
DMG="src-tauri/target/<arch>-apple-darwin/release/bundle/dmg/Voice Typer_1.0.0_<arch>.dmg"

# Sign the DMG.
codesign --force --sign "$IDENTITY" "$DMG"

# Notarize.
xcrun notarytool submit "$DMG" \
    --apple-id "$APPLE_ID" \
    --password "$APPLE_PASSWORD" \
    --team-id "$APPLE_TEAM_ID" \
    --wait

# Staple.
xcrun stapler staple "$DMG"
xcrun stapler validate "$DMG"
```

### Verify

```bash
# Final verification — must all pass.
codesign --verify --verbose=4 "$APP"
spctl --assess --verbose=4 "$APP"
xcrun stapler validate "$APP"
codesign --verify --verbose=4 "$DMG"
spctl --assess --verbose=4 "$DMG"
xcrun stapler validate "$DMG"
```

### Apple Silicon + Intel

Per ADR-0020 §13.2: build separately, produce two `.app` bundles, ship
as two DMGs (or one universal DMG). The existing `electron-builder.yml`
ships `dmg` with `arch: [x64, arm64]` — two separate DMGs. Mirror this
for Tauri:

```bash
# Build + sign + notarize + staple for each arch separately:
bash scripts/build/build_tauri_all.sh macos x86_64-apple-darwin
bash scripts/build/build_tauri_all.sh macos aarch64-apple-darwin
# (Sign + notarize + staple each per the commands above.)
```

### Cross-reference

- Sub-agent #6's macOS validation runbook: `macos-validation-runbook.md`
  (per-platform runbook — owned by sub-agent #6).
- `.github/workflows/build.yml` macOS job: existing `MAC_SIGNING_IDENTITY`
  + `APPLE_TEAM_ID` env wiring.

---

## Linux — unsigned by default (ADR-0020 §13.3)

### Why unsigned

Linux desktop apps have **no certificate authority** comparable to
Windows Authenticode or macOS Developer ID. The distro package managers
(`apt`, `dnf`) verify GPG signatures on the **repository** level, not
on individual packages. Today's Electron build (`electron-builder.yml`)
ships unsigned `.deb` / `.rpm` / AppImage; the Tauri build matches this.

### Optional GPG-signing (out of scope for v1)

For users who want to verify package integrity, the following are
documented but NOT implemented in v1:

- **GPG-sign the `.deb`**: `dpkg-sig --sign builder <deb>`. Users
  verify with `apt-key` (deprecated) or `debsig-verify`.
- **GPG-sign the `.rpm`**: `rpm --addsign <rpm>`. Users verify with
  `rpm --checksig`.
- **AppImage GPG signature**: AppImage supports `zsync` + GPG; documented
  at the AppImage spec.

Track these as a follow-up after the Tauri cutover stabilizes.

### User installation (no signing = some manual steps)

| Format | Install command | Notes |
|--------|-----------------|-------|
| `.deb` | `sudo dpkg -i voice-typer-1.0.0-linux-amd64.deb && sudo apt-get -f install` | `-f install` resolves missing deps (libnotify4, libxtst6, etc.). |
| `.rpm` | `sudo dnf install voice-typer-1.0.0-linux-x86_64.rpm` | `dnf` resolves deps automatically. |
| AppImage | `chmod +x VoiceTyper-1.0.0-linux-x86_64.AppImage && ./VoiceTyper-*.AppImage` | Runs without install; persists settings to `~/.config/voice-typer/`. |

### Reused Linux package scripts (ADR-0020 §13.3)

The existing `scripts/linux/` scripts are **NOT Tauri-specific** and are
reused verbatim for the Tauri `.deb` / `.rpm` bundles:

| Script | Purpose | Wired in `tauri.conf.json` `bundle.linux` |
|--------|---------|-------------------------------------------|
| `scripts/linux/postinst` | `.deb` post-install: udev rule, `input` group, Caps Lock neutralization, `/var/lib/voice-typer/permissions-manifest.json`. | `deb.postInstallScript` |
| `scripts/linux/prerm` | `.deb` pre-remove: clean up the manifest. | `deb.preRemoveScript` |
| `scripts/linux/postinst.rpm` | `.rpm` post-install (same as `postinst`, RPM syntax). | `rpm.postInstallScript` |
| `scripts/linux/prerm.rpm` | `.rpm` pre-remove (same as `prerm`, RPM syntax). | `rpm.preRemoveScript` |
| `scripts/linux/99-voice-typer.rules` | udev rule for the native hotkey binary. | Installed by `postinst`/`postinst.rpm`. |
| `scripts/linux/00-voice-typer-capslock.conf` | X11 Caps Lock config. | Installed by `postinst`/`postinst.rpm`. |
| `scripts/linux/voice-typer.polkit` | polkit policy for AppImage `pkexec`. | Used by the AppImage launcher. |

> **Do NOT modify these scripts for the Tauri build.** They are shared
> with the Electron fallback path. Per ADR-0020 §"Kept verbatim".

### Cross-reference

- Sub-agent #7's Linux validation runbook: `linux-validation-runbook.md`
  (per-platform runbook — owned by sub-agent #7).
- `voice_typer/client/electron-builder.yml` `deb`/`rpm` sections: existing
  `afterInstall`/`afterRemove` wiring (the Tauri `bundle.linux.deb/rpm`
  config mirrors these).

---

## No auto-update (ADR-0020 §15)

### Decision

Per ADR-0020 §15: **auto-update is out of scope for the v1 Tauri
migration.** Ship the Tauri build as a manual-download release (matching
today's Electron release model — there is no working auto-update today).
Track auto-update as a separate follow-up ADR after the Tauri cutover
stabilizes. Do **NOT** wire up `tauri-plugin-updater` in the v1 migration.

### Rationale (ADR-0020 §15)

`tauri-plugin-updater` is the cross-platform auto-updater (Windows
replaces MSI via `nsis`; macOS replaces DMG via `sparkle`-style; Linux
replaces AppImage via `AppImageUpdate`). It requires:

1. A `latest.json` manifest hosted at a stable URL.
2. A signing keypair (private key in CI, public key in the app).

Both are orthogonal to the runtime migration (Electron → Tauri) and add
a signing-key distribution problem + a manifest-hosting problem. The v1
migration is scoped to the runtime swap only.

### Audit results

The audit was performed by sub-agent #10 (this document's owner) on
2026-07-16. The audit covers all files under `src-tauri/` (the Tauri
host source tree) + the docs/ADRs that reference `updater` for context.

#### Files audited

| File | Result |
|------|--------|
| `src-tauri/Cargo.toml` | ✅ **CLEAN** — no `tauri-plugin-updater` dependency. |
| `src-tauri/tauri.conf.json` | ✅ **CLEAN** — no `plugins.updater` key. The `plugins` object contains only `notification`, `clipboard-manager`, `single-instance`, `shell`. |
| `src-tauri/capabilities/main-runtime.json` | ✅ **CLEAN** — no `updater:*` permissions. The `permissions` array contains only `core:*`, `shell:*`, `notification:*`, `clipboard-manager:*` entries. `bubble-runtime.json` (the bubble-window sibling) was also audited in the same pass — it likewise contains no updater permissions. |
| `src-tauri/src/main.rs` | ✅ **CLEAN** — no `updater` references (not touched by this sub-agent per task rules). |

#### Doc references (NOT modified — context only)

The following files reference `updater` for **design / context** reasons
and are intentionally NOT modified by this audit:

| File | Lines | Why it references `updater` | Action |
|------|-------|-----------------------------|--------|
| `docs/adr/0020-desktop-runtime-migration-analysis.md` | 136, 631, 807, 809, 991 | Authoritative spec — §15 explicitly decides NOT to wire `tauri-plugin-updater`. The reference at line 631 is inside an EXAMPLE `tauri.conf.json` snippet (showing what the config WOULD look like if updater were wired — for context, not as a build target). | No action — the ADR is the source of truth for the no-updater decision. |
| `docs/adr/0013-desktop-runtime-migration-analysis.md` | 133, 146, 147 | The PRIOR (superseded) ADR — references `updater` as a hypothetical option. ADR-0020 supersedes ADR-0013. | No action — superseded ADR. |
| `docs/API.md` | 123 | Mentions `electron-updater` (Electron's auto-updater, not Tauri's) in a comment about a config flag. | No action — Electron-side reference. |
| `docs/auto-update-feature.md` | 10, 28, 58-67, 180-186 | Design-only spec for the (not-implemented) auto-update feature. The file's own header states: "STATUS: NOT IMPLEMENTED." | No action — design doc only. |
| `voice_typer/client/electron-builder.yml` | 8 | Comment about `auto-updater` (Electron's). | No action — Electron-side config; the `publish: github` block is not consumed by any code today (per ADR-0020 §15). |

#### Conclusion

The Tauri v1 build is **confirmed clean of `tauri-plugin-updater`**.
No code, config, or capability changes were needed. Future maintainers
should NOT add `tauri-plugin-updater` to:

- `src-tauri/Cargo.toml` `[dependencies]`
- `src-tauri/tauri.conf.json` `plugins.updater`
- `src-tauri/capabilities/main-runtime.json` `permissions` (and `bubble-runtime.json`)

If a future release decides to wire auto-update, file a new ADR
(superseding the §15 decision) and update this audit section.

### Guard comment in `Cargo.toml`

The following comment is added to `src-tauri/Cargo.toml` to prevent
future maintainers from accidentally adding the updater plugin. (No
functional change — just a comment.)

> This guard comment was NOT added in this round because the existing
> `Cargo.toml` already has clear section comments + the absence of the
> dep is itself the signal. The audit results above are the canonical
> "do not add updater" reference. A future round may add the comment
> if maintainer confusion arises.

---

## CI wiring

The per-platform CI workflows (`.github/workflows/tauri-<platform>-build.yml`,
owned by sub-agents #5/#6/#7) consume the signing env vars above and run
the signing commands in the appropriate order. The top-level
`.github/workflows/tauri-build.yml` orchestrator passes `sign: true`
through to the per-platform workflows when the user selects it.

For the env var names to use in CI, see §"Reused signing identities"
above — they are the same names the existing Electron build uses, so
no new secrets need to be created.

---

## See also

- [`tauri-build-runbook.md`](./tauri-build-runbook.md) — master index for
  the Tauri build pipeline.
- [`cutover-playbook.md`](./cutover-playbook.md) — per-platform cutover
  procedure + rollback + mixed-mode support.
- [`tauri-sidecar-bridge.md`](./tauri-sidecar-bridge.md) — the wire
  contract between the Rust host and the Python sidecar.
- [`../adr/0020-desktop-runtime-migration-analysis.md`](../adr/0020-desktop-runtime-migration-analysis.md)
  — §13 (signing) + §15 (no auto-update) are the authoritative sources
  for this guide.
