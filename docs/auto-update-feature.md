<!--STATUS: NOT IMPLEMENTED. This is a design-only spec. -->
<!-- None of the referenced files exist. Do not reference this document as if the feature ships. -->

## Auto-Update System Architecture (Design Spec — Tauri v2)

> **STATUS: NOT IMPLEMENTED (design only).** This document is a design
> spec for a future auto-update feature built on Tauri v2 primitives.
> No code exists for any of the described components. Do not reference
> this as a shipping feature. If you want to implement auto-update,
> start here and use [`tauri-plugin-updater`](https://v2.tauri.app/plugin/updater/)
> + a `latest.json` manifest served from GitHub Releases.
>
> This is the **Tauri v2** design. The earlier Electron-based design
> (which used `electron-builder`'s built-in `publish` + `autoUpdater`
> APIs) is obsolete now that the desktop host has migrated to Tauri
> (ADR-0020). Those env vars / Electron-only primitives are flagged
> "(Electron — ignored under Tauri v2)" in [`debugging.md`](debugging.md).

────────────────────────────────────────────────────────────────────────────────
## Architecture Overview

The auto-update system would consist of three components (none yet
implemented):

1. **Update runner** (Rust host, `src-tauri/src/`) — drives
   `tauri-plugin-updater` from the Rust side: checks the published
   `latest.json` manifest, downloads the signed update artifact in the
   background, verifies the signature, and orchestrates the silent
   install + relaunch sequence via `AppHandle::restart()`.
2. **React hook** (renderer) — subscribes to update status changes via
   Tauri events and exposes `checkNow()`, `installNow()`, and
   `ignore()` to UI components.
3. **Update banner** (renderer component) — a persistent sticky
   notification that appears when an update is downloaded and ready to
   install.

The Rust host would use [`tauri-plugin-updater`](https://v2.tauri.app/plugin/updater/),
which handles the `latest.json` manifest fetch, signature verification,
background download, and atomic install. The manifest is published as a
static JSON file alongside the release artifacts (GitHub Releases /
Pages). Combined with a pre-download strategy: when a new version is
detected, the installer is downloaded immediately in the background so
that when the user clicks "Update Now", the install takes 3-5 seconds
instead of requiring a download.

────────────────────────────────────────────────────────────────────────────────
## `latest.json` manifest

Each release publishes a `latest.json` manifest at a stable URL (e.g.
`https://github.com/<owner>/<repo>/releases/latest/download/latest.json`).
The shape is defined by `tauri-plugin-updater`:

```json
{
  "version": "1.2.3",
  "notes": "Release notes for 1.2.3",
  "pub_date": "2026-08-05T12:00:00Z",
  "platforms": {
    "windows-x86_64": {
      "signature": "<minisign signature of the .nsis installer>",
      "url": "https://github.com/<owner>/<repo>/releases/download/v1.2.3/VoiceTyper-Setup-1.2.3.exe"
    },
    "darwin-aarch64": {
      "signature": "<minisign signature of the .app.tar.gz>",
      "url": "https://github.com/<owner>/<repo>/releases/download/v1.2.3/VoiceTyper-1.2.3.arm64.app.tar.gz"
    },
    "darwin-x86_64": {
      "signature": "<minisign signature of the .app.tar.gz>",
      "url": "https://github.com/<owner>/<repo>/releases/download/v1.2.3/VoiceTyper-1.2.3.x64.app.tar.gz"
    },
    "linux-x86_64": {
      "signature": "<minisign signature of the .AppImage>",
      "url": "https://github.com/<owner>/<repo>/releases/download/v1.2.3/voice-typer-1.2.3-amd64.AppImage"
    }
  }
}
```

The signature is produced offline with the project's minisign private
key. The Tauri host embeds only the **public** key (in
`tauri.conf.json` → `plugins.updater.pubkey`) and verifies each
downloaded artifact before applying it. A mismatched signature aborts
the update with a visible error — there is no silent fallthrough.

────────────────────────────────────────────────────────────────────────────────
## State Machine

The Rust-side update runner would implement the following state machine:

- **IDLE** → **CHECKING** (timer fires every 6h or manual "Check Now")
- **CHECKING** → **UP_TO_DATE** (current version matches latest)
- **CHECKING** → **UPDATE_AVAILABLE** (new version found → start download)
- **UPDATE_AVAILABLE** → **DOWNLOADING** (immediately)
- **DOWNLOADING** → **DOWNLOADED** (100% complete + signature verified)
- **DOWNLOADING** → **ERROR** (network failure OR signature mismatch)
- **DOWNLOADED** → **INSTALLING** (user clicks "Update Now" → `AppHandle::restart()`)
- **DOWNLOADED** → **IGNORED** (user clicks "Ignore")
- **INSTALLING** → (process exits, Tauri swaps the bundle, app relaunches)
- **ERROR** → **IDLE** (next check cycle)

────────────────────────────────────────────────────────────────────────────────
## IPC / Tauri command surface

### Renderer → Rust (`#[tauri::command]`)

| Command | Payload | Returns | Purpose |
|---|---|---|---|
| `updater_check_now` | — | `UpdateStatus` | Force a check (manual button) |
| `updater_get_status` | — | `UpdateStatus` | Get current state (on mount) |
| `updater_install_now` | — | `{ success: boolean }` | Trigger install + `AppHandle::restart()` |
| `updater_ignore_version` | `{ version: string }` | — | Dismiss for this version |

### Rust → Renderer (`app_handle.emit(...)`)

| Event | Payload | When |
|---|---|---|
| `updater://status-changed` | `UpdateStatus` | Any state transition |

### TypeScript types (proposed)

```typescript
type UpdateState =
  | 'idle'
  | 'checking'
  | 'up_to_date'
  | 'update_available'
  | 'downloading'
  | 'downloaded'
  | 'installing'
  | 'ignored'
  | 'error'

interface UpdateStatus {
  state: UpdateState
  currentVersion: string
  latestVersion?: string
  downloadProgress?: number       // 0–100
  lastCheckedAt?: number          // epoch ms
  error?: string
}
```

────────────────────────────────────────────────────────────────────────────────
## Pre-Download Strategy (Key Design Decision)

When a background check finds a new version:
1. State transitions to `update_available`
2. `tauri-plugin-updater` begins downloading the artifact immediately
   in the Rust async runtime (no UI prompt required).
3. Download progress is streamed to the renderer via
   `updater://status-changed` events.
4. When complete + signature-verified, state transitions to `downloaded`.
5. User clicks "Update Now" → `AppHandle::restart()` → the plugin
   swaps the bundle on disk and relaunches the app — no separate
   installer process and no shell-out to `/S`.

Total time from clicking "Update Now" to app relaunch: ~3-5 seconds.

────────────────────────────────────────────────────────────────────────────────
## Install & Relaunch Sequence

The install sequence would:
1. Emit `installing` state so UI shows "Updating..."
2. Call `AppHandle::restart()` — the Tauri runtime handles the swap
   atomically (the new bundle is already downloaded + verified).
3. The current process exits; Tauri's relauncher spawns the new bundle
   with the same working directory + env.

Edge case: If the downloaded artifact was deleted (disk cleanup), the
plugin falls back to re-downloading on the next check cycle. If the
manifest URL is unreachable, the runner opens the GitHub releases page
in the user's default browser.

────────────────────────────────────────────────────────────────────────────────
## Persistence (Survive App Restart)

Since checks happen every 6 hours, the runner needs to remember:
- `lastCheckedAt` — stored in `tauri-plugin-store` (a small JSON file
  under the platform-specific config dir — see
  [`docs/home-directory.md`](home-directory.md)).
- `ignoredVersion` — stored in `tauri-plugin-store`.
- Downloaded installer path — `tauri-plugin-updater`'s internal cache
  directory; checked for existence on startup.

On startup:
1. Read `lastCheckedAt` from the store.
2. If more than 6 hours ago → trigger background check.
3. If less → schedule next check for `lastCheckedAt + 6h`.
4. If a previously downloaded + verified artifact exists in the
   updater's cache → restore `downloaded` state.

────────────────────────────────────────────────────────────────────────────────
## UI Components (proposed)

### Update Banner — Persistent In-App Notification

A sticky banner between the TitleBar and page content that appears
when state is `downloaded`. Shows the new version number and has
"Ignore" and "Update Now" buttons. Never auto-dismisses.

### Settings Page Section

An "Updates & Version" section in Settings showing:
- Current version
- Auto-check toggle (switch)
- "Check Now" button (60s cooldown)
- "Last checked: Xh ago" text
- Update status (up-to-date or update available)

────────────────────────────────────────────────────────────────────────────────
## Scheduling & Rate Limiting

| Check type | Interval | Cooldown |
|---|---|---|
| Background (auto) | Every 6 hours | N/A |
| Manual (button) | On demand | 60 seconds between clicks |
| GitHub Releases manifest fetch | 60 req/hour (unauthenticated) | Well within budget (max ~9 req/day) |

────────────────────────────────────────────────────────────────────────────────
## Edge Cases & Error Handling

| Scenario | Behavior |
|---|---|
| No internet on check | Silently fail, retry next interval |
| GitHub API rate limited | Retry next interval (6h) |
| Download interrupted | Plugin deletes partial file, retries next check cycle |
| Signature mismatch | Abort the update, emit `error` state with "signature mismatch" |
| Ignored version released | Only resurface when a newer version is found |
| User reinstalled manually | Version changes, stale downloaded state clears |
| Temp file deleted by cleaner | Plugin re-downloads on next "Update Now" |
| Manifest unreachable | Open GitHub releases page in browser |
| App open twice | Tauri `single-instance` plugin rejects the second instance |
| Auto-check disabled | Only check on manual button click |

────────────────────────────────────────────────────────────────────────────────
## File Impact Summary (proposed, none exist yet)

**New files (Rust host):**
1. `src-tauri/src/updater/mod.rs` — Rust-side update runner wrapping `tauri-plugin-updater`.
2. `src-tauri/src/updater/commands.rs` — `#[tauri::command]` handlers (`updater_check_now`, `updater_get_status`, `updater_install_now`, `updater_ignore_version`).
3. `src-tauri/src/updater/state.rs` — `UpdateRunnerState` shared via `tauri::State`.

**New files (renderer):**
1. `voice_typer/client/src/renderer/src/hooks/useUpdater.ts` — React hook subscribing to `updater://status-changed`.
2. `voice_typer/client/src/renderer/src/components/UpdateBanner.tsx` — Banner component.

**Modified files (Rust host):**
1. `src-tauri/Cargo.toml` — add `tauri-plugin-updater` + `tauri-plugin-store` deps.
2. `src-tauri/tauri.conf.json` — `plugins.updater` config (manifest URL + embedded public key).
3. `src-tauri/src/main.rs` — register the updater plugin + commands on the `tauri::Builder`.
4. `src-tauri/capabilities/main-runtime.json` — grant the renderer `updater:default` + `store:default` permissions.

**Modified files (renderer):**
1. `voice_typer/client/src/renderer/src/App.tsx` — Mount UpdateBanner.
2. `voice_typer/client/src/renderer/src/pages/Settings.tsx` — Add Updates section.

**CI / release pipeline:**
1. `.github/workflows/release.yml` — add a `publish-latest-json` step that builds the manifest from the per-platform artifacts + signs them with the project minisign key (the private key is held in GitHub Actions secrets).
2. `scripts/build/compile_native.sh` — no change (the updater ships with the Tauri host, not the native hotkey binary).

────────────────────────────────────────────────────────────────────────────────
## Why This Architecture Is Correct

1. **Native Tauri primitives** — `tauri-plugin-updater` is the
   upstream-recommended path under Tauri v2; reinventing it would
   re-implement signature verification + atomic install + cross-platform
   bundle handling.
2. **Pre-download** — Download happens in background hours before user clicks "Update Now"
3. **Persistent banner** — Never auto-dismisses; user must explicitly act
4. **Signature verification** — minisign signatures are checked by the plugin before the swap; a tampered artifact is rejected hard
5. **Clean state machine** — Every state explicit; UI maps 1:1 to states
6. **Graceful degradation** — No network? Shows version. Download fails? Retries. Manifest unreachable? Falls back to the releases page.
7. **User in control** — Auto-check can be disabled; manual check always available
