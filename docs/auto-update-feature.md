<!-- DOC-012: STATUS: NOT IMPLEMENTED. This is a design-only spec. -->
<!-- None of the referenced files (updater.ts, useUpdater.ts, UpdateBanner.tsx) exist. -->
<!-- Do not reference this document as if the feature ships. -->

## Auto-Update System Architecture (Design Spec)

> **STATUS: NOT IMPLEMENTED (design only).** This document is a design
> spec for a future auto-update feature. No code exists for any of the
> described components (`updater.ts`, `useUpdater.ts`,
> `UpdateBanner.tsx`). Do not reference this as a shipping feature.
> If you want to implement auto-update, start here and use
> `electron-builder`'s built-in `publish` + `autoUpdater` APIs.

────────────────────────────────────────────────────────────────────────────────
## Architecture Overview
┌─────────────────────────────────────────────────────────────────┐
│                      Electron App                               │
│                                                                 │
│  ┌─────────────────────┐          ┌──────────────────────────┐  │
│  │   Main Process      │          │   Renderer Process       │  │
│  │                     │          │                          │  │
│  │  ┌───────────────┐  │   IPC    │  ┌────────────────────┐  │  │
│  │  │   updater.ts  │◄─┼──────────┼─►│  useUpdater.ts     │  │  │
│  │  │               │  │          │  │  (React Hook)      │  │  │
│  │  │  • check()    │  │          │  └────────┬───────────┘  │  │
│  │  │  • download() │  │          │           │              │  │
│  │  │  • install()  │  │          │           ▼              │  │
│  │  │  • state mgr  │  │          │  ┌────────────────────┐  │  │
│  │  └───────┬───────┘  │          │  │  UpdateBanner.tsx  │  │  │
│  │          │          │          │  │  (persistent notif)│  │  │
│  │          ▼          │          │  └────────────────────┘  │  │
│  │  ┌───────────────┐  │          │                          │  │
│  │  │   GitHub API  │  │          │  ┌────────────────────┐  │  │
│  │  │  (HTTPS fetch)│  │          │  │  Settings.tsx      │  │  │
│  │  └───────┬───────┘  │          │  │  • manual check    │  │  │
│  │          │          │          │  │  • version display │  │  │
│  │          ▼          │          │  └────────────────────┘  │  │
│  │  ┌───────────────┐  │          └──────────────────────────┘  │
│  │  │   Temp Dir    │  │                                        │
│  │  │  (installer)  │  │                                        │
│  │  └───────┬───────┘  │                                        │
│  │          │          │                                        │
│  │          ▼          │                                        │
│  │  ┌───────────────┐  │                                        │
│  │  │  NSIS /S      │  │  → app.quit() → installer runs         │
│  │  │  (silent)     │  │  → relaunch app after install          │
│  │  └───────────────┘  │                                        │
│  └─────────────────────┘                                        │
└─────────────────────────────────────────────────────────────────┘
────────────────────────────────────────────────────────────────────────────────
1. Core Module —  updater.ts  (Electron Main Process)
State Machine
                    ┌─────────────────┐
                    │      IDLE       │ ◄──── startup + interval
                    └────────┬────────┘
                             │ check trigger (manual or timer)
                             ▼
                    ┌─────────────────┐
                    │    CHECKING     │ ──► GitHub API fetch
                    └────────┬────────┘
                             │
                    ┌────────┴────────┐
                    │                 │
                    ▼                 ▼
           ┌────────────────┐  ┌────────────────┐
           │  UP_TO_DATE    │  │ UPDATE_AVAILABLE│
           │ (transient →   │  │ (new version   │
           │  auto-dismiss) │  │  found)        │
           └────────────────┘  └────────┬────────┘
                                        │ start download immediately
                                        ▼
                               ┌────────────────┐
                               │  DOWNLOADING   │
                               │ (show progress)│
                               └────────┬────────┘
                                        │ download complete
                                        ▼
                               ┌────────────────┐
                               │  DOWNLOADED    │
                               │ (waiting for   │
                               │  user action)  │
                               │                │
                               │  [Ignore] → IGNORED
                               │  [Update] → INSTALLING
                               └────────┬────────┘
                                        │ user clicks "Update Now"
                                        ▼
                               ┌────────────────┐
                               │  INSTALLING    │
                               │ • app.quit()   │
                               │ • spawn /S     │
                               │ • relaunch     │
                               └────────────────┘
State Transitions Detail
┌──────────────────┬──────────────────┬────────────────────────────────────────┬───────────────────────────────────────────────────────────┐
│ From             │ To               │ Trigger                                │ Side Effects                                              │
├──────────────────┼──────────────────┼────────────────────────────────────────┼───────────────────────────────────────────────────────────┤
│ IDLE             │ CHECKING         │ Timer fires (6h) or manual "Check Now" │ Clear previous download; fetch GitHub API                 │
│ CHECKING         │ UP_TO_DATE       │ tag_name == app.getVersion()           │ Emit status to renderer; auto-dismiss after 5s            │
│ CHECKING         │ UPDATE_AVAILABLE │ tag_name > current version             │ Emit status; start download immediately                   │
│ UPDATE_AVAILABLE │ DOWNLOADING      │ Auto (immediately)                     │ Download installer to temp dir                            │
│ DOWNLOADING      │ DOWNLOADED       │ 100% complete                          │ Emit status; store download path                          │
│ DOWNLOADING      │ ERROR            │ Network failure mid-download           │ Emit error; retry on next check cycle                     │
│ DOWNLOADED       │ INSTALLING       │ User clicks "Update Now"               │ Execute install sequence                                  │
│ DOWNLOADED       │ IGNORED          │ User clicks "Ignore"                   │ Store ignored version; suppress until next higher version │
│ INSTALLING       │ (exit)           │ Installer launched                     │ app.quit() → installer runs → relaunch                    │
│ ERROR            │ IDLE             │ Next check cycle                       │ Clean up partial download                                 │
└──────────────────┴──────────────────┴────────────────────────────────────────┴───────────────────────────────────────────────────────────┘
────────────────────────────────────────────────────────────────────────────────
2. IPC Messages
Renderer → Main ( ipcMain.handle )
┌────────────────────────┬─────────────────────┬──────────────────────┬───────────────────────────────────────┐
│ Channel                │ Payload             │ Returns              │ Purpose                               │
├────────────────────────┼─────────────────────┼──────────────────────┼───────────────────────────────────────┤
│ updater:check-now      │ —                   │ UpdateStatus         │ Force a check (manual button)         │
│ updater:get-status     │ —                   │ UpdateStatus         │ Get current state (on mount)          │
│ updater:install-now    │ —                   │ { success: boolean } │ Trigger install + relaunch            │
│ updater:ignore-version │ { version: string } │ —                    │ Dismiss notification for this version │
└────────────────────────┴─────────────────────┴──────────────────────┴───────────────────────────────────────┘
Main → Renderer ( webContents.send )
┌────────────────────────┬──────────────┬──────────────────────┐
│ Channel                │ Payload      │ When                 │
├────────────────────────┼──────────────┼──────────────────────┤
│ updater:status-changed │ UpdateStatus │ Any state transition │
└────────────────────────┴──────────────┴──────────────────────┘
Types
// typescript
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
────────────────────────────────────────────────────────────────────────────────
3. Pre-Download Strategy (Key Design Decision)
This is the user's core insight, and it's correct:
┌───────────────────────────────┬───────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ Event                         │ What happens                                                                                                  │
├───────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ Background check finds v1.1.2 │ State → update_available                                                                                      │
│ Immediately after             │ Start downloading the .exe in the main process using https.get or electron-net                                │
│ Download progress             │ Streamed to temp file, percentage sent to renderer                                                            │
│ Download completes            │ State → downloaded. Temp file path stored in memory                                                           │
│ User clicks "Update Now"      │ No download needed — just runs the already-downloaded installer → app quits → installer runs → app relaunches │
└───────────────────────────────┴───────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
Total time for user from clicking "Update Now" to app relaunch: ~3–5 seconds (just the installer execution).
Download location:  app.getPath('temp')/voice-typer-update/  — created on first download, cleaned on success.
────────────────────────────────────────────────────────────────────────────────
4. Install & Relaunch Sequence
This is the trickiest part. Here's the exact sequence:
// typescript
async function installAndRelaunch(installerPath: string) {
  // 1. Emit 'installing' state so UI shows "Updating..."
  // 2. Quit the app (releases file locks)
  // 3. Use a background shell command:
  //      - Wait 2s (for app to fully exit)
  //      - Run installer silently:  installer.exe /S
  //      - Wait for installer to finish
  //      - Launch the updated app:  start "" "installed-path\Voice Typer.exe"
 
  const cmd = [
    `timeout /t 2 /nobreak >nul`,
    `"${installerPath}" /S`,
    `start "" "${process.execPath}"`,
  ].join(' && ')
 
  exec(cmd, { detached: true, windowsHide: true, shell: 'cmd.exe' })
  app.quit()
}
Why this works:
1.  app.quit()  releases file locks immediately
2. The shell command waits 2 seconds for clean shutdown
3. NSIS  /S  flag installs silently to the original install path
4.  process.execPath  points to the newly updated executable
5.  start ""  launches it in a new window
Edge case — installer not found: If the temp file was deleted (disk cleanup), fall back to opening the GitHub releases page in the browser.
────────────────────────────────────────────────────────────────────────────────
5. Persistence (Survive App Restart)
Since checks happen every 6 hours, the app needs to remember:
┌─────────────────────────┬───────────────────────────────────┬─────────────────────────────────────────────────────────────────────────────┐
│ What                    │ Where                             │ Why                                                                         │
├─────────────────────────┼───────────────────────────────────┼─────────────────────────────────────────────────────────────────────────────┤
│ lastCheckedAt           │ electron-store or local JSON file │ Show "Last checked: 2h ago"                                                 │
│ ignoredVersion          │ electron-store                    │ Don't re-notify about ignored version                                       │
│ downloadedInstallerPath │ Temp dir file                     │ If user restarts while in downloaded state, check if installer still exists │
└─────────────────────────┴───────────────────────────────────┴─────────────────────────────────────────────────────────────────────────────┘
On app startup:
1. Read  lastCheckedAt  from store
2. If more than 6 hours ago → trigger background check
3. If less → schedule next check for  lastCheckedAt + 6h 
4. Check if a previously downloaded installer exists → if yes, restore  downloaded  state
────────────────────────────────────────────────────────────────────────────────
6. UI Components
 UpdateBanner.tsx  — Persistent In-App Notification
┌─────────────────────────────────────────────────────────┐
│  🚀   Voice Typer v1.1.2 is ready to install           │
│       New features, bug fixes, and improvements         │
│                                              ┌────────┐ │
│                         [Ignore for now]     │ Update │ │
│                                              │  Now   │ │
│                                              └────────┘ │
└─────────────────────────────────────────────────────────┘
- Position: Between the TitleBar and the page content (app-level layout, not page-level)
- Visibility: Only when state is  downloaded 
- Persistence: Never auto-dismisses
- Ignore action: Transitions to  ignored  state; doesn't show again for this version
- Update action: Transitions to  installing ; quits app
Settings Page Section
┌─────────────────────────────────────────────────────┐
│  Updates & Version                                  │
│                                                     │
│  Current version: v1.0.0                            │
│                                                     │
│  Check for updates automatically                    │
│  [Switch: ON]                    ┌───────────────┐  │
│                                  │  Check Now    │  │
│                                  └───────────────┘  │
│  Last checked: 2 hours ago                          │
│                                                     │
│  [If up-to-date:]                                   │
│  ✓  You have the latest version (v1.0.0)            │
│                                                     │
│  [If update available:]                             │
│  ⚠  v1.1.2 ready to install  ┌──────────────────┐  │
│                               │  Update Now      │  │
│                               └──────────────────┘  │
└─────────────────────────────────────────────────────┘
- The "Check Now" button triggers a manual check
- Has a 60-second cooldown to prevent GitHub API spam
- Shows the download progress if in  downloading  state
 useUpdater.ts  — React Hook
// typescript
function useUpdater() {
  const [status, setStatus] = useState<UpdateStatus>(...)
 
  useEffect(() => {
    // On mount: get current status
    window.electron.updater.getStatus().then(setStatus)
 
    // Subscribe to status changes
    const unsubscribe = window.electron.updater.onStatusChange(setStatus)
    return unsubscribe
  }, [])
 
  return {
    ...status,
    checkNow: () => window.electron.updater.checkNow(),
    installNow: () => window.electron.updater.installNow(),
    ignore: () => window.electron.updater.ignoreVersion(status.latestVersion!),
  }
}
────────────────────────────────────────────────────────────────────────────────
7. Scheduling & Rate Limiting
┌───────────────────┬───────────────────────────────┬─────────────────────────────────────────────────────────────────┐
│ Check type        │ Interval                      │ Cooldown                                                        │
├───────────────────┼───────────────────────────────┼─────────────────────────────────────────────────────────────────┤
│ Background (auto) │ Every 6 hours (21,600,000 ms) │ N/A                                                             │
│ Manual (button)   │ On demand                     │ 60 seconds between clicks                                       │
│ GitHub API limit  │ 60 req/hour (unauthenticated) │ Well within budget (max 5 req/day for auto + occasional manual) │
└───────────────────┴───────────────────────────────┴─────────────────────────────────────────────────────────────────┘
Why 6 hours?
- Short enough to catch updates within a day
- Long enough to never hit GitHub's 60 req/hour rate limit
- Even if user manually checks 5 times, that's only 9 requests/day
────────────────────────────────────────────────────────────────────────────────
8. Edge Cases & Error Handling
┌──────────────────────────────┬───────────────────────────────────────────────────────────────────────────────┐
│ Scenario                     │ Behavior                                                                      │
├──────────────────────────────┼───────────────────────────────────────────────────────────────────────────────┤
│ No internet on check         │ Silently fail, retry next interval                                            │
│ GitHub API rate limited      │ Retry next interval (6h)                                                      │
│ Download interrupted         │ Delete partial file, retry next check cycle                                   │
│ Ignored version released     │ Only resurface when newer version is found                                    │
│ User reinstalled manually    │ Version changes, stale downloaded state clears                                │
│ Temp file deleted by cleaner │ Fall back to downloading fresh on "Update Now" click (show progress)          │
│ Installer fails to run       │ Show error in Settings; open GitHub releases page as fallback                 │
│ App open twice               │ Kill other instance before install (already handled by single-instance mutex) │
│ Auto-check disabled          │ Only check on manual button click (no background checks)                      │
└──────────────────────────────┴───────────────────────────────────────────────────────────────────────────────┘
────────────────────────────────────────────────────────────────────────────────
9. File Impact Summary
New files (3):
┌─────────────────────────────────────────────────────────────────┬─────────────────────────────────────────────────────────────┐
│ File                                                            │ Purpose                                                     │
├─────────────────────────────────────────────────────────────────┼─────────────────────────────────────────────────────────────┤
│ voice_typer/client/src/main/updater.ts                          │ Core update logic (check, download, install, state machine) │
│ voice_typer/client/src/renderer/src/hooks/useUpdater.ts         │ React hook wrapping IPC                                     │
│ voice_typer/client/src/renderer/src/components/UpdateBanner.tsx │ Persistent sticky notification banner                       │
└─────────────────────────────────────────────────────────────────┴─────────────────────────────────────────────────────────────┘
Modified files (4):
┌────────────────────────────────────────────────────────┬───────────────────────────────────────────────────────────────────┐
│ File                                                   │ Changes                                                           │
├────────────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────┤
│ voice_typer/client/src/main/index.ts                   │ Import and initialize updater on app startup; handle IPC channels │
│ voice_typer/client/src/preload/index.ts                │ Expose updater IPC methods                                        │
│ voice_typer/client/src/renderer/src/App.tsx            │ Mount UpdateBanner between TitleBar and page content              │
│ voice_typer/client/src/renderer/src/pages/Settings.tsx │ Add "Updates & Version" section                                   │
└────────────────────────────────────────────────────────┴───────────────────────────────────────────────────────────────────┘
────────────────────────────────────────────────────────────────────────────────
10. Why This Architecture Is Correct
1. Free infrastructure — Only GitHub public API + existing Releases. No servers to pay for.
2. Pre-download — The user's insight. Download happens in background hours before user clicks "Update Now". The click just triggers install.
3. Persistent banner — Never auto-dismisses. User must explicitly ignore or update. No missed notifications.
4. No SmartScreen bypass needed — The  /S  silent install still works; SmartScreen is a one-time warning per version that fades as more users install.
5. Clean state machine — Every state is explicit. UI maps 1:1 to states. No race conditions.
6. Graceful degradation — No network? Still shows version. Download fails? Retries next cycle. Temp file missing? Downloads fresh.
7. User in control — Auto-check can be disabled. Manual check always available. Ignore works per-version.