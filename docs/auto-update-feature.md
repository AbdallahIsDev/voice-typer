<!-- DOC-012 / DOC-040: STATUS: NOT IMPLEMENTED. This is a design-only spec. -->
<!-- None of the referenced files exist. Do not reference this document as if the feature ships. -->

## Auto-Update System Architecture (Design Spec)

> **STATUS: NOT IMPLEMENTED (design only).** This document is a design
> spec for a future auto-update feature. No code exists for any of the
> described components. Do not reference this as a shipping feature.
> If you want to implement auto-update, start here and use
> `electron-builder`'s built-in `publish` + `autoUpdater` APIs.

────────────────────────────────────────────────────────────────────────────────
## Architecture Overview

The auto-update system would consist of three components (none yet
implemented):

1. **Update Manager** (Electron main process) — checks GitHub Releases
   API, downloads the installer in the background, and orchestrates the
   silent install + relaunch sequence.
2. **React Hook** (renderer) — subscribes to update status changes via
   IPC and exposes `checkNow()`, `installNow()`, and `ignore()` to
   UI components.
3. **Update Banner** (renderer component) — a persistent sticky
   notification that appears when an update is downloaded and ready to
   install.

The main process would use `electron-builder`'s built-in `autoUpdater`
API (which handles GitHub Releases publishing) combined with a
pre-download strategy: when a new version is detected, the installer
is downloaded immediately in the background so that when the user
clicks "Update Now", the install takes 3-5 seconds instead of
requiring a download.

────────────────────────────────────────────────────────────────────────────────
## State Machine

The update manager would implement the following state machine:

- **IDLE** → **CHECKING** (timer fires every 6h or manual "Check Now")
- **CHECKING** → **UP_TO_DATE** (current version matches latest)
- **CHECKING** → **UPDATE_AVAILABLE** (new version found → start download)
- **UPDATE_AVAILABLE** → **DOWNLOADING** (immediately)
- **DOWNLOADING** → **DOWNLOADED** (100% complete)
- **DOWNLOADING** → **ERROR** (network failure)
- **DOWNLOADED** → **INSTALLING** (user clicks "Update Now")
- **DOWNLOADED** → **IGNORED** (user clicks "Ignore")
- **INSTALLING** → (process exits, installer runs, app relaunches)
- **ERROR** → **IDLE** (next check cycle)

────────────────────────────────────────────────────────────────────────────────
## IPC Messages

### Renderer → Main (`ipcMain.handle`)

| Channel | Payload | Returns | Purpose |
|---|---|---|---|
| `updater:check-now` | — | `UpdateStatus` | Force a check (manual button) |
| `updater:get-status` | — | `UpdateStatus` | Get current state (on mount) |
| `updater:install-now` | — | `{ success: boolean }` | Trigger install + relaunch |
| `updater:ignore-version` | `{ version: string }` | — | Dismiss for this version |

### Main → Renderer (`webContents.send`)

| Channel | Payload | When |
|---|---|---|
| `updater:status-changed` | `UpdateStatus` | Any state transition |

### TypeScript Types (proposed)

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
2. The installer download starts immediately in the main process
3. Download progress is streamed to a temp file
4. When complete, state transitions to `downloaded`
5. User clicks "Update Now" → installer runs (no download needed) → app quits → installer runs → app relaunches

Total time from clicking "Update Now" to app relaunch: ~3-5 seconds.

────────────────────────────────────────────────────────────────────────────────
## Install & Relaunch Sequence

The install sequence would:
1. Emit `installing` state so UI shows "Updating..."
2. Quit the app (releases file locks)
3. Use a background shell command that:
   - Waits 2s for clean shutdown
   - Runs installer silently: `installer.exe /S`
   - Launches the updated app: `start "" "installed-path\Voice Typer.exe"`
4. The shell command runs detached so it survives `app.quit()`

Edge case: If the temp installer was deleted (disk cleanup), fall back
to opening the GitHub releases page in the browser.

────────────────────────────────────────────────────────────────────────────────
## Persistence (Survive App Restart)

Since checks happen every 6 hours, the app needs to remember:
- `lastCheckedAt` — stored in electron-store or local JSON file
- `ignoredVersion` — stored in electron-store
- `downloadedInstallerPath` — temp file; checked for existence on startup

On startup:
1. Read `lastCheckedAt` from store
2. If more than 6 hours ago → trigger background check
3. If less → schedule next check for `lastCheckedAt + 6h`
4. Check if a previously downloaded installer exists → restore `downloaded` state

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
| GitHub API limit | 60 req/hour (unauthenticated) | Well within budget (max ~9 req/day) |

────────────────────────────────────────────────────────────────────────────────
## Edge Cases & Error Handling

| Scenario | Behavior |
|---|---|
| No internet on check | Silently fail, retry next interval |
| GitHub API rate limited | Retry next interval (6h) |
| Download interrupted | Delete partial file, retry next check cycle |
| Ignored version released | Only resurface when newer version is found |
| User reinstalled manually | Version changes, stale downloaded state clears |
| Temp file deleted by cleaner | Fall back to downloading fresh on "Update Now" |
| Installer fails to run | Show error in Settings; open GitHub releases page |
| App open twice | Kill other instance (single-instance mutex) |
| Auto-check disabled | Only check on manual button click |

────────────────────────────────────────────────────────────────────────────────
## File Impact Summary (proposed, none exist yet)

**New files (3):**
1. `voice_typer/client/src/main/updater.ts` — Core update logic
2. `voice_typer/client/src/renderer/src/hooks/useUpdater.ts` — React hook
3. `voice_typer/client/src/renderer/src/components/UpdateBanner.tsx` — Banner

**Modified files (4):**
1. `voice_typer/client/src/main/index.ts` — Initialize updater on startup
2. `voice_typer/client/src/preload/index.ts` — Expose updater IPC
3. `voice_typer/client/src/renderer/src/App.tsx` — Mount UpdateBanner
4. `voice_typer/client/src/renderer/src/pages/Settings.tsx` — Add Updates section

────────────────────────────────────────────────────────────────────────────────
## Why This Architecture Is Correct

1. **Free infrastructure** — Only GitHub public API + existing Releases
2. **Pre-download** — Download happens in background hours before user clicks "Update Now"
3. **Persistent banner** — Never auto-dismisses; user must explicitly act
4. **No SmartScreen bypass** — `/S` silent install works; SmartScreen fades per version
5. **Clean state machine** — Every state explicit; UI maps 1:1 to states
6. **Graceful degradation** — No network? Shows version. Download fails? Retries.
7. **User in control** — Auto-check can be disabled; manual check always available
