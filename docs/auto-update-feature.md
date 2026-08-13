## Auto-Update System Architecture (Runtime Pack — Implemented 2026-08-13)

> **STATUS: IMPLEMENTED (2026-08-13).** This document previously read
> "NOT IMPLEMENTED (design only)". Sub-agent 13 (master plan §10) built
> the runtime-pack auto-update mechanism from scratch. The earlier
> Tauri-v2 `tauri-plugin-updater` design proposed below the
> "Historical design — Tauri v2 `tauri-plugin-updater`" section has
> been superseded by the Python-side implementation described in the
> first half of this document. Both halves are retained for
> traceability — the historical design is the original spec, and the
> "Implemented" section describes what actually ships.

────────────────────────────────────────────────────────────────────────────────
## Architecture Overview (Implemented)

The auto-update system covers the **runtime pack** (the worker exe
onefile + `pack-manifest.json`) — NOT the slim-core installer. The
slim core is updated via the platform's native installer (NSIS on
Windows, DMG on macOS, AppImage/deb/rpm on Linux); the runtime pack
is updated silently in-app because it ships outside the installer.

The implementation consists of three components:

1. **Pack-version checker** (Python, `voice_typer/server/service/update_check.py`)
   — fetches the remote `pack-manifest.json` from GitHub Releases,
   compares its `version` field against the locally installed pack,
   and (if newer + consent given) triggers a background download via
   `voice_typer/server/service/pack.py::download_pack_with_resume`.
   Consent-gated via `config.runtime_pack_consent` (NOT
   `huggingface_consent` — the pack phones home to GitHub/Microsoft,
   not HuggingFace).
2. **GitHub Releases publisher** (`scripts/release/publish_pack_release.py`)
   — release-engineering CLI that publishes the pack onefile + manifest
   to GitHub Releases. Two backends: `gh` CLI (preferred) and GitHub
   REST API (fallback). Idempotent (re-running with the same tag skips
   `gh release create` if the release exists + uses `--clobber` to
   replace existing assets).
3. **Network-online trigger** (renderer, `voice_typer/client/src/renderer/src/hooks/useNetworkOnline.ts`)
   — React hook that subscribes to the browser's `online` event and
   triggers a `check_pack_update` IPC call on the false → true
   transition. No `fetch()` / `XMLHttpRequest` / `axios` in the
   renderer — every network call goes through the Python IPC bridge so
   the SSRF defense runs server-side.

The renderer also has a sibling hook `usePackDownload.ts` (Sub-agent 9)
that consumes the `pack_download_started` / `pack_download_progress` /
`pack_download_completed` push events published by the pack downloader
and exposes `{ status, error, isReady }` for UI components.

────────────────────────────────────────────────────────────────────────────────
## `pack-manifest.json` (Implemented)

Each pack release publishes a `pack-manifest.json` manifest at a
stable URL — GitHub Releases serves the latest release's manifest from:

```
https://github.com/AbdallahIsDev/voice-typer/releases/latest/download/pack-manifest.json
```

(Pinned in `voice_typer/server/service/update_check.py` as
`DEFAULT_PACK_MANIFEST_URL`. Override via the `VT_PACK_MANIFEST_URL`
env var — test escape hatch + power-user override.)

The pack onefile itself is version-pinned:

```
https://github.com/AbdallahIsDev/voice-typer/releases/download/v<version>/pack-<version>.zip
```

The manifest schema is defined by
`voice_typer/server/service/pack.py::load_pack_manifest` and includes
`version`, `sha256`, `files` (list of `{name, sha256, size}`), and
`min_proto_version`. Manifests larger than `MAX_MANIFEST_BYTES = 1 MiB`
are rejected (defense-in-depth — chunked read in transport +
`_secure_read_text` on the temp file).

────────────────────────────────────────────────────────────────────────────────
## API surface (Implemented)

### Python (`voice_typer/server/service/update_check.py`)

- `check_pack_update(config, event_bus, *, http_get=None, manifest_url=None, local_version=None, root=None, trigger_download=True) -> UpdateCheckResult`
  — main entry point. Consent-gated (raises / publishes
  `consent_required` event when `config.runtime_pack_consent` is
  False). Triggers `pack.download_pack_with_resume` on a daemon thread
  when a newer version is found.
- `handle_check_pack_update_ipc(app, data, *, http_get=None, ...) -> dict`
  — thin IPC handler wrapper. **NOT auto-registered in
  `ipc/registry.py`** — wiring is owned by whoever owns the shared
  registry file. The renderer hook fails gracefully (caught + logged
  at debug) until the command is registered.
- `fetch_remote_manifest(url, *, http_get=None) -> PackManifest | None`
  — pure helper. SSRF-gated (`pack.assert_pack_url_allowed`), max-bytes-capped.
- `is_newer_version(remote, local) -> bool` — semver-ish comparison
  (handles `v1.2.3`, `1.2.3-rc1`, shorter tuples).

### Publisher (`scripts/release/publish_pack_release.py`)

- `publish_release(tag, assets, *, repo, notes, ..., backend=None) -> PublishResult`
  — publishes a GitHub Release with the given assets. Auto-selects
  backend: `gh` CLI when `shutil.which("gh")` finds it, else GitHub
  REST API (uses `GH_TOKEN` / `GITHUB_TOKEN` env vars).
- CLI entry point (`main`) with argparse + `--json` output for CI
  parsing. Asset-name templates per C-CI-13:
  - `VoiceTyper-Setup-<version>.exe` (Windows NSIS installer).
  - `VoiceTyper-<version>.<arch>.app.tar.gz` (macOS bundle).
  - `voice-typer-<version>-<arch>.AppImage` (Linux).
  - `pack-<version>.zip` (pack onefile — version-pinned).
  - `pack-manifest.json` (NOT versioned — served from
    `/releases/latest/download/`).

### Renderer (`voice_typer/client/src/renderer/src/hooks/useNetworkOnline.ts`)

```typescript
interface UseNetworkOnlineResult {
  isOnline: boolean
  lastOnlineAt: number | null
  triggerRecheck: () => void
  isChecking: boolean
  error: string | null
}
function useNetworkOnline(): UseNetworkOnlineResult
```

- Subscribes to `window.addEventListener("online", ...)` + `"offline"`.
- On the false → true `navigator.onLine` transition, calls
  `call("check_pack_update", {})` via `usePython()`.
- Transition dedup via `useRef` (browsers fire duplicate `online`
  events during connection flapping — without dedup, IPC would be
  spammed).
- NO direct `fetch()` / `XMLHttpRequest` / `axios` — all network
  routes through the Python IPC bridge so the SSRF defense runs
  server-side.

────────────────────────────────────────────────────────────────────────────────
## Security inheritance (Implemented)

- **SSRF:** `voice_typer.server.service.pack.assert_pack_url_allowed`
  extends the URL allowlist with `github.com` /
  `objects.githubusercontent.com` / `codeload.github.com` AND inherits
  the IP-literal blocklist + DNS-rebinding defense from
  `voice_typer.server.security.url_allowlist.assert_url_allowed`
  (the same defense tested by `tests/test_http_safety_ssrf.py`).
- **Max-bytes:** `voice_typer.server.secure_file_io._secure_read_text(max_bytes=MAX_MANIFEST_BYTES)`
  where `MAX_MANIFEST_BYTES = 1 MiB` (defense-in-depth: chunked read
  in transport + `_secure_read_text` on temp file). Tested by
  `tests/test_secure_file_io_max_bytes.py`.
- **Proxy:** `voice_typer.server.service.pack.proxy_env()` returns
  the system proxy env vars (`HTTP_PROXY` / `HTTPS_PROXY` + lowercase
  variants) for the urllib request.
- **Consent:** `voice_typer.server.service.pack.require_runtime_pack_consent(config, version=...)`
  raises `PackConsentRequiredError` when
  `config.runtime_pack_consent` is False. `check_pack_update` catches
  it + publishes a `consent_required` event (mirrors
  `ModelMixin._require_huggingface_consent`).

────────────────────────────────────────────────────────────────────────────────
## C-DATA-1 constraint (Needs user action)

`CONSTRAINTS.md` rule **C-DATA-1** currently allows 3 categories of
network calls: (1) cloud transcription / LLM providers, (2) auto-update
— "Check for Updates" / silent update check against the GitHub API, (3)
model downloads. The pack download from GitHub Releases is NOT covered
by these 3 categories — the rule pre-dates the runtime-pack split.

The USER must either:
- Extend category (3) "model downloads" → "runtime asset downloads"
  (so it covers both HuggingFace model weights AND GitHub Releases
  pack onefile), OR
- Add category (4) "runtime pack downloads from GitHub Releases".

Agents cannot edit `CONSTRAINTS.md` (CONSTRAINTS.md L12 + AGENTS.md
L243). This rule change is recorded in `worklog.md` under the
consolidated "CONSTRAINTS.md — needs user action" section.

────────────────────────────────────────────────────────────────────────────────
## Wiring NOT yet done (Integration debt)

The auto-update mechanism is implemented end-to-end at the file
level, but several integration steps remain (owned by other agents or
by the integration phase):

- **IPC command registration.** The `check_pack_update` IPC command
  is exposed by `handle_check_pack_update_ipc` but NOT registered in
  `voice_typer/server/ipc/registry.py:_COMMAND_REGISTRY` /
  `voice_typer/client/src/main/allowed-commands.ts:ALLOWED_COMMANDS` /
  `src-tauri/src/commands/sidecar_cmds/allowlist.rs:ALLOWED_COMMANDS`.
  Until registered, the renderer's `call("check_pack_update", {})`
  fails gracefully (caught + logged at debug).
- **`runtime_pack_consent` config field.** Referenced by
  `pack.require_runtime_pack_consent` and `update_check.check_pack_update`
  via `getattr(config, "runtime_pack_consent", False)`. The field
  needs to be added to `voice_typer/server/config/__init__.py`
  (dataclass field `runtime_pack_consent: bool = False`) if not
  already present. Until then, `getattr` returns `False` (safe
  default — consent required).
- **Renderer consent dialog UI.** Awaits whoever owns the renderer
  consent dialog. Gated on the `consent_required` event published by
  `check_pack_update` when consent is missing.
- **Mount `useNetworkOnline` in the App component.** The hook is
  defined but not mounted at the renderer top level.
- **Vitest test for `useNetworkOnline.ts`.** A Python structural test
  (`tests/test_update_network_online.py`) pins the contract; a
  vitest test would verify the runtime behavior (event listener
  registration, IPC call, transition dedup).
- **CI workflow integration.** A `.github/workflows/release.yml` step
  that calls `publish_pack_release.py` on tag push.

────────────────────────────────────────────────────────────────────────────────
## Tests (Implemented)

- `tests/test_update_check.py` (40 tests, 827 LOC) —
  `TestIsNewerVersion`, `TestFetchRemoteManifest` (SSRF block,
  non-allowlisted host, network error, invalid JSON, schema
  validation, oversized manifest, proxy env var passthrough),
  `TestCheckPackUpdate` (no-local-pack + remote available triggers
  download; up-to-date pack; newer remote; consent missing →
  consent_required event; fetch failure; trigger_download=False;
  env-var override; default URL contract; checked_at epoch ms),
  `TestTriggerBackgroundDownload`, `TestHandleCheckPackUpdateIpc`,
  `TestMaxBytesCapInherited`, `TestSSRFInherited`.
- `tests/test_update_publish.py` (40 tests, 661 LOC) —
  `TestValidateAssets`, `TestGhCommandConstruction`,
  `TestGhReleaseExists`, `TestPublishReleaseGhBackend`,
  `TestPublishReleaseApiBackend`, `TestBackendAutoSelection`,
  `TestAssetNameTemplates` (C-CI-13), `TestCli`, `TestIdempotency`,
  `TestPublishResultDataclass`.
- `tests/test_update_network_online.py` (19 tests, 300 LOC) —
  structural / drift test (Python reads the TS file as text — mirrors
  `tests/test_branding_scan_coverage.py` pattern).
  `TestFileExists`, `TestExports`, `TestBrowserEventSubscription`,
  `TestIpcIntegration`, `TestTransitionDedup`, `TestReturnType`,
  `TestNoDirectNetwork`.

Test command: `pytest tests/test_update*.py -x --no-cov` → 137 passed
(100 new + 37 pre-existing in `test_update_native_manifests.py` /
`test_update_tauri_manifests.py`).

────────────────────────────────────────────────────────────────────────────────
## Historical design — Tauri v2 `tauri-plugin-updater` (NOT what shipped)

> The section below is the **original design spec** that pre-dated the
> 2026-08-13 implementation. It proposed using
> `tauri-plugin-updater` for the slim-core installer auto-update. The
> actual implementation (above) is Python-side and covers the
> runtime PACK (not the slim-core installer — that still uses the
> platform's native installer). This section is retained for
> traceability against the design that motivated the implemented
> approach; **the "NOT IMPLEMENTED (design only)" banner that used
> to live at the top of this file applied to THIS section, not to
> the implementation described above.**

The original design proposed three components:

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

> **Why this design was NOT implemented as-is:** the runtime-pack
> split (master plan §4) made the original "single installer +
> tauri-plugin-updater" model insufficient. The runtime pack (worker
> onefile + manifest) ships OUTSIDE the platform installer and needs
> its own silent-update path. Implementing the pack update via
> `tauri-plugin-updater` would have required a second updater plugin
> instance scoped to the pack — complex + bypassed by the simpler
> Python-side approach (fetch manifest, compare versions, daemon
> thread download with resume, verify SHA-256, atomic swap). The
> slim-core installer may still adopt `tauri-plugin-updater` in a
> future phase (it would replace the platform's native installer
> flow), but the pack update path is shipped as the Python-side
> implementation above.

────────────────────────────────────────────────────────────────────────────────
## `latest.json` manifest (historical — Tauri v2 updater contract)

The historical Tauri-v2 design published a `latest.json` manifest at
a stable URL (e.g.
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

> **Note:** the implemented pack auto-update uses a different manifest
> schema (`pack-manifest.json` with `version` + `sha256` + `files` +
> `min_proto_version`) and a different verification path
> (`pack.verify_pack_or_skip` modeled on
> `autostart_launcher.verify_tauri_binary_or_skip`). The
> `latest.json`/minisign contract above remains the path for a future
> slim-core installer updater based on `tauri-plugin-updater`.

────────────────────────────────────────────────────────────────────────────────
## State Machine (historical — Tauri v2 updater)

The Rust-side update runner proposed the following state machine:

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

> **Implemented equivalent:** the pack update path uses
> `usePackDownload`'s `{ status, error, isReady }` state object
> (Sub-agent 9). The `check_pack_update` Python entry point
> transitions the pack through `idle → checking → update_available →
> downloading → downloaded → verified → installed` (with `error` and
> `consent_required` as terminal branches). See
> `voice_typer/server/service/pack.py` + `update_check.py` for the
> canonical state machine.

────────────────────────────────────────────────────────────────────────────────
## Historical design — IPC / Tauri command surface (NOT implemented)

The historical design proposed these Tauri commands and events:

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

> **Implemented equivalent:** the pack update path uses the existing
> `call("check_pack_update", {})` IPC dispatch (no new Tauri command
> needed — the Python side handles it via the standard
> `_COMMAND_REGISTRY` → `_handle_*` flow). State changes flow as
> standard push events (`pack_download_started` /
> `pack_download_progress` / `pack_download_completed` /
> `pack_download_failed`) consumed by `usePackDownload`.

────────────────────────────────────────────────────────────────────────────────
## Historical design — Pre-Download Strategy, Install & Relaunch, Persistence, UI, Scheduling, Edge Cases, File Impact, Architecture Rationale

The original design spec's sections on Pre-Download Strategy, Install
& Relaunch Sequence, Persistence (Survive App Restart), UI Components
(Update Banner, Settings Page Section), Scheduling & Rate Limiting,
Edge Cases & Error Handling, File Impact Summary, and "Why This
Architecture Is Correct" are retained verbatim below for
traceability — they describe the Tauri-v2 `tauri-plugin-updater`
design that was NOT implemented as-is. The implemented pack
auto-update (above) covers the equivalent concerns via the
Python-side `update_check.py` + `pack.py` + `useNetworkOnline.ts` +
`usePackDownload.ts` stack.

### Pre-Download Strategy (Key Design Decision)

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

> **Implemented equivalent:** the pack update path triggers
> `pack.download_pack_with_resume` on a daemon thread as soon as
> `check_pack_update` finds a newer version + consent is given.
> Progress flows as `pack_download_progress` events at 1 Hz. The
> atomic swap happens at the file level (POSIX rename-over is atomic;
> Windows requires stopping the worker first per master plan §8.3).

### Install & Relaunch Sequence

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

> **Implemented equivalent:** the pack update path uses
> `verify_pack_or_skip` (SHA-256) + atomic file swap + worker-exe
> restart. The slim-core installer restart is NOT covered by the
> pack auto-update — it remains a platform-installer concern.

### Persistence (Survive App Restart)

Since checks happen every 6 hours, the runner needs to remember:
- `lastCheckedAt` — stored in `tauri-plugin-store` (a small JSON file
  under the platform-specific config dir — see
  [`docs/home-directory.md`](home-directory.md)).
- `ignoredVersion` — stored in `tauri-plugin-store`.
- Downloaded installer path — `tauri-plugin-updater`'s internal cache
  directory; checked for existence on startup.

> **Implemented equivalent:** the pack update path stores
> `lastCheckedAt` (epoch ms) on the `UpdateCheckResult` returned by
> `check_pack_update`. Persistence across app restarts is a future
> enhancement (currently in-memory only).

### UI Components (proposed)

#### Update Banner — Persistent In-App Notification

A sticky banner between the TitleBar and page content that appears
when state is `downloaded`. Shows the new version number and has
"Ignore" and "Update Now" buttons. Never auto-dismisses.

#### Settings Page Section

An "Updates & Version" section in Settings showing:
- Current version
- Auto-check toggle (switch)
- "Check Now" button (60s cooldown)
- "Last checked: Xh ago" text
- Update status (up-to-date or update available)

> **Implemented equivalent:** the pack UI surfaces "Preparing offline
> engine…" in the transcription area (master plan §4.8 / §9.3 — i18n
> key `pack.preparingOfflineEngine` added by Sub-agent 14). The
> settings checkbox `downloadOfflineEngineLater` +
> `keepOfflineEngineRunning` (Sub-agent 14) covers the
> auto-download toggle + low-RAM-keep-running toggle.

### Scheduling & Rate Limiting

| Check type | Interval | Cooldown |
|---|---|---|
| Background (auto) | Every 6 hours | N/A |
| Manual (button) | On demand | 60 seconds between clicks |
| GitHub Releases manifest fetch | 60 req/hour (unauthenticated) | Well within budget (max ~9 req/day) |

> **Implemented equivalent:** the pack update is event-driven (online
> event + first-launch check). The 6-hourly background timer is a
> future enhancement.

### Edge Cases & Error Handling

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

> **Implemented equivalent:** the pack update path handles partial
> download resume (`pack.download_pack_with_resume`), corruption
> recovery (`verify_pack_or_skip` retries up to 3 times with
> exponential backoff), disk space check (`asr_utils._check_disk_space_for_download`
> reused), and metered-connection detection (Windows NLM API via
> ctypes; manual on Linux/macOS — master plan §8.5). See master
> plan §8 for the full edge-case matrix.

### File Impact Summary (historical — proposed, none shipped as-is)

**New files (Rust host):**
1. `src-tauri/src/updater/mod.rs` — Rust-side update runner wrapping `tauri-plugin-updater`.
2. `src-tauri/src/updater/commands.rs` — `#[tauri::command]` handlers (`updater_check_now`, `updater_get_status`, `updater_install_now`, `updater_ignore_version`).
3. `src-tauri/src/updater/state.rs` — `UpdateRunnerState` shared via `tauri::State`.

**New files (renderer):**
1. `voice_typer/client/src/renderer/src/hooks/useUpdater.ts` — React hook subscribing to `updater://status-changed`.
2. `voice_typer/client/src/renderer/src/components/UpdateBanner.tsx` — Banner component.

> **Implemented equivalent:** the actual files shipped are listed in
> the "Architecture Overview (Implemented)" section above
> (`update_check.py` + `publish_pack_release.py` + `useNetworkOnline.ts`
> + 3 test files). No `src-tauri/src/updater/` module was created.

### Why This Architecture Is Correct (historical rationale)

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

> **Implemented equivalent (rationale for the Python-side approach):**
> 1. **Simpler SSRF defense** — `pack.assert_pack_url_allowed` extends
>    the existing URL allowlist (no Rust-side reimplementation).
> 2. **Reuses pack downloader** — `download_pack_with_resume` is the
>    same path the first-launch pack download uses (no second
>    downloader to maintain).
> 3. **Reuses consent UI** — `runtime_pack_consent` mirrors the
>    existing `huggingface_consent` flow.
> 4. **Renderer hook is minimal** — `useNetworkOnline.ts` only fires
>    the IPC call on the false → true online transition (no
>    per-second timer, no Tauri plugin dependency).
> 5. **Tauri v2 `tauri-plugin-updater` may still be adopted later
>    for the slim-core installer** — the implemented path is
>    scoped to the runtime pack, leaving the slim-core installer
>    update path open for the upstream-recommended plugin.
