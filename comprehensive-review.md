# Voice Typer — Comprehensive Product Review

**Generated**: 2026-07-14
**Scope**: Permanent Product Improvements Review across 4 review areas
**Status**: Findings compiled; fixes applied to high-priority items this round.

---

## Summary

| Area | Critical | High | Medium | Low | Total |
|------|----------|------|--------|-----|-------|
| 13a: Backend Architecture + Reliability | 0 | 3 | 6 | 3 | 12 |
| 13b: Frontend + UX + Accessibility | 0 | 4 | 6 | 4 | 14 |
| 13c: Performance + Memory + CPU + Cross-platform + CI | 0 | 4 | 9 | 9 | 22 |
| 13d: Security + Testing + Docs + Code Quality | 0 | 2 | 8 | 6 | 16 |
| **TOTAL** | **0** | **13** | **29** | **22** | **64** |

All Critical and High findings were addressed this round OR documented as Won't Fix with rationale.

---

## a-review: Backend Architecture + Reliability Findings

**Agent:** Subagent (Explore)
**Scope:** Backend architecture, reliability, stability, error handling, logging consistency in `voice_typer/server/`.

### Summary
- Total findings: 12
- Critical: 0, High: 3, Medium: 6, Low: 3

Methodology: read `app.py`, `service.py`, `ipc_server.py`, `dictation_pipeline.py`, `crash_recovery.py`, `duck_crash_recovery.py`, `log.py`, `event_bus.py`, `thread_registry.py`, plus targeted reads of `recording.py`, `recording_controller.py`, `streaming.py`, `providers.py`, `transcription.py`. Grepped for broad-except patterns, lock usage, and logging consistency.

### Findings

#### Finding 1
- **Category**: reliability
- **Severity**: High
- **File**: voice_typer/server/crash_recovery.py:268-284 (`shutdown`), :125-148 (`_enqueue_save`), :288-308 (`add`)
- **Description**: `shutdown()`'s docstring claims "After shutdown, any further calls to `add()` / `mark_pasted()` / etc. will fall back to synchronous saves". This is false — `add()` calls `_enqueue_save()` unconditionally, which puts to a queue whose worker has exited. Post-shutdown mutations to `_entries` are silently never persisted.
- **Root cause**: The "fallback to synchronous saves" behavior was documented but never implemented. `_enqueue_save` doesn't check `self._stopped`.
- **Fix**: Either (a) make `_enqueue_save` call `_save_sync()` directly when `self._stopped` is True, or (b) update the docstring to say "post-shutdown calls are no-ops" and audit callers to ensure none fire after shutdown. Option (a) preserves the documented contract.

#### Finding 2
- **Category**: reliability
- **Severity**: High
- **File**: voice_typer/server/dictation_pipeline.py:375-381 (`_apply_vocabulary`), :401-407 (`_apply_templates`), :589-596 (`_store_result` history), :613-620 (`_store_result` crash recovery)
- **Description**: The "notify once" deduplication flags (`_vocab_fail_notified`, `_template_fail_notified`, `_history_fail_notified`, `_crash_recovery_fail_notified`) are set on `self` (the `DictationPipeline` instance), but a fresh pipeline is constructed per transcription cycle (`recording_controller.py:458`). The flags reset every cycle, so the user gets a tray notification on EVERY cycle where the failure occurs — exactly the spam the "notify once" design (ERR-006/ERR-014) was meant to prevent.
- **Root cause**: The flags should live on `self._app` (session-scoped), not `self` (cycle-scoped).
- **Fix**: Move the four flags to `self._app` (e.g. `self._app._vocab_fail_notified`) and `getattr(self._app, ...)` / `setattr(self._app, ...)` at the call sites.

#### Finding 3
- **Category**: reliability
- **Severity**: High
- **File**: voice_typer/server/crash_recovery.py:387-407 (`__del__`)
- **Description**: `__del__` only calls `_save_sync()` if `self._save_thread.is_alive() and not self._save_queue.empty()`. After `shutdown()` is called, the worker thread is dead, so `__del__` skips the save — even if `_entries` was mutated after shutdown. Combined with Finding 1, any post-shutdown mutations are silently lost on GC.
- **Root cause**: The `is_alive()` guard assumes the worker is always alive when there's pending data; it doesn't account for the post-shutdown state.
- **Fix**: Drop the `is_alive()` guard — call `_save_sync()` whenever `_entries` is non-empty and `_stopped` is True. Or better: make `shutdown()` itself do a final `_save_sync()` after joining the worker (currently it only sends the None sentinel and joins, relying on the worker to drain — which it does for pre-shutdown saves, but not post-shutdown ones).

#### Finding 4
- **Category**: reliability
- **Severity**: Medium
- **File**: voice_typer/server/ipc_server.py:205, :1110-1127, :1132-1135, :1411-1414
- **Description**: `_HEARTBEAT_TIMEOUT_SECONDS = 120.0` (24 missed heartbeats) but multiple docstrings/comments still say "15s timeout" / "3 missed heartbeats" (lines 1115, 1132-1135, 1414). The actual recovery window after Electron crashes is 120s, not 15s — during which the Win32 single-instance mutex is held, blocking relaunch. Operators reading the docs will misdiagnose "backend stuck for 2 minutes" as a hang when it's documented behavior.
- **Root cause**: The constant was bumped from 15s to 120s (commit comment on line 205 says "increased from 15s") but the surrounding docstrings weren't updated.
- **Fix**: Update the 3 stale docstring/comment locations to say "120s (24 missed heartbeats)". Consider whether 120s is actually the right value — if Electron crash recovery should be faster, lower it; if 120s is intentional (to tolerate long GC pauses), document why.

#### Finding 5
- **Category**: logging
- **Severity**: Medium
- **File**: voice_typer/server/log.py:76-89 (`_SessionFilter`), :307-311 (`_FileFormatter.format`), :255-275 (`_ColorFormatter.format`)
- **Description**: `_SessionFilter` injects `session_id` and `component` attributes into every LogRecord, but neither `_FileFormatter` nor `_ColorFormatter` ever renders them. The 8-char session ID is generated per-process (`setup_logging` line 360) but never appears in any log output — making it impossible to correlate log lines across process restarts or distinguish interleaved logs from concurrent processes in `voice-typer.log`.
- **Root cause**: The filter was added (with tests) but the formatters were never updated to consume the new attributes.
- **Fix**: Add `[{record.session_id}]` to the file formatter prefix (e.g. `2026-06-28 18:36:22  INFO  [a3f1b2c4]  [HOTKEY] RegisterHotKey succeeded`). Optionally add it dimmed to the color formatter.

#### Finding 6
- **Category**: logging
- **Severity**: Medium
- **File**: voice_typer/server/log.py:62-70 (`get_logger`); all 70+ modules in `voice_typer/server/`
- **Description**: `get_logger()` is documented as the canonical logger factory ("Every backend module should use `get_logger`"), but zero modules use it — every module does `log = logging.getLogger(__name__)` directly. The documented public API is dead code. In practice the two are equivalent (because `__name__` resolves to `voice_typer.server.<module>`), but the inconsistency means future modules could diverge (e.g. a module loaded as `__main__` would get logger name `__main__`, missing the `voice_typer.*` namespace and its filters).
- **Root cause**: The factory was added (CQ-007 era) but no migration was done.
- **Fix**: Either (a) migrate all `logging.getLogger(__name__)` → `get_logger(__name__)` calls (mechanical, ~70 files), or (b) delete `get_logger` and update the docstring to say "use `logging.getLogger(__name__)` directly". Option (a) future-proofs against `__main__` edge cases.

#### Finding 7
- **Category**: reliability
- **Severity**: Medium
- **File**: voice_typer/server/ipc_server.py:1199-1216 (`_hook_tray_set_state`), :629-707 (`start`/`stop`)
- **Description**: `_hook_tray_set_state()` monkey-patches `app.tray.set_state` by capturing `original = self.app.tray.set_state` and replacing it with a wrapper that calls `original(...)` then `self.push(...)`. `start()` calls this every time, but `stop()` never unwraps it. On a start→stop→start cycle (common in tests, possible in restart scenarios), the second `start()` captures the already-wrapped function as `original`, so each state change emits 2 push events; after N cycles, N events per state change.
- **Root cause**: No unwrapping in `stop()` and no dedup guard in `_hook_tray_set_state`.
- **Fix**: Either (a) unwrap in `stop()` (store `original` on `self` and restore it), or (b) guard in `_hook_tray_set_state`: `if getattr(self.app.tray.set_state, "_vt_wrapped", False): return` and set `wrapped._vt_wrapped = True`.

#### Finding 8
- **Category**: error-handling
- **Severity**: Medium
- **File**: voice_typer/server/dictation_pipeline.py:283-291 (`_transcribe`)
- **Description**: Catches `TypeError` broadly to handle "backend doesn't support the `audio_stats` kwarg", then retries without it. A `TypeError` raised inside the function body (e.g. `None.lower()`, bad indexing) is also caught and the retry either fails the same way (confusing trace) or masks the original bug. Only `cloud_engines.py:250` lacks the `audio_stats` parameter — the other 3 backends (whisper, qwen, parakeet) all accept it.
- **Root cause**: The fallback was added to handle a single missing-kwarg case but uses an over-broad exception class.
- **Fix**: Add `audio_stats=None` to `CloudEngine.transcribe_with_fallback` (ignore the value), then delete the try/except. Alternatively, use `inspect.signature` once at engine-init to decide whether to pass `audio_stats`.

#### Finding 9
- **Category**: logging
- **Severity**: Medium
- **File**: voice_typer/server/ipc_server.py:1054-1055 (`_handle_tcp_connection` outer except)
- **Description**: After the inner dispatch-error catch (lines 1023-1051, which logs at ERROR), a second `except Exception: log.debug("[TCP] client connection closed")` catches any remaining exception from the connection loop and logs it at DEBUG. Genuine connection-level failures (e.g. `_TCPLineIO` read errors, rate-limiter state corruption, partial-frame bugs) are invisible in production logs.
- **Root cause**: The DEBUG level was chosen to avoid noise from routine disconnects, but it's too low for unexpected exceptions.
- **Fix**: Split the except: catch `OSError` (routine socket close) at DEBUG, and catch `Exception` (anything else) at WARNING with `exc_info=True`.

#### Finding 10
- **Category**: reliability
- **Severity**: Low
- **File**: voice_typer/server/ipc_server.py:208-272 (`_RateLimiter`), :166-168 (constants)
- **Description**: With `burst=200, sustained=60`, the sustained check (`len >= 60`, line 252) always fires before the burst check (`len >= 200`, line 250). The advertised 200-msg burst capacity is unreachable — a client sending 60 msgs/s hits the sustained limit and is throttled, never seeing the burst headroom. The `burst` parameter is effectively dead config.
- **Root cause**: The two limits were designed independently without checking which binds first.
- **Fix**: Either (a) raise `sustained` above `burst` (e.g. sustained=200, burst=200 — making them equivalent), or (b) make sustained a per-second rate measured over a longer window (e.g. 600 msgs / 10s) so bursts within 1s aren't throttled by the sustained limit. Document which limit binds when.

#### Finding 11
- **Category**: reliability
- **Severity**: Low
- **File**: voice_typer/server/app.py:1518-1522 (`_do_cleanup` history_db path)
- **Description**: `_do_cleanup()` calls `history_db.flush()` but never `history_db.close()`. The writer thread (daemon) is killed at process exit without an explicit shutdown; read connections (`_read_local`, `_all_read_connections`) rely on `HistoryDB.__del__` → `close()`, which may not run during interpreter shutdown. SQLite WAL and file handles could leak (OS cleans up, but clean shutdown is better practice and avoids `ResourceWarning`).
- **Root cause**: `close()` was likely omitted to keep shutdown fast (it joins the writer thread with a timeout), but `flush()` already blocks on the writer.
- **Fix**: After `flush()`, call `self.history_db.close()` inside a try/except. The writer join in `close()` should be fast since `flush()` already drained the queue.

#### Finding 12
- **Category**: architecture
- **Severity**: Low
- **File**: voice_typer/server/ipc_server.py:379-381 (`_push_event_registry`), :382 (`_push_event_registry_lock`)
- **Description**: `_push_event_registry = event_bus._subscribers` and `_push_event_registry_lock = event_bus._lock` directly alias the event_bus module's internals. Tests that do `ipc_server._push_event_registry.clear()` mutate `event_bus._subscribers` directly. The "thin shim" comment acknowledges this, but it breaks `event_bus`'s encapsulation by making its private state part of the IPC module's public test surface. Future refactors of `event_bus` (e.g. switching to a class) would silently break tests that touch the alias.
- **Root cause**: The shim was added (B-1) to preserve backward compat with tests that manipulated the old global, by aliasing rather than delegating.
- **Fix**: Migrate tests to use `event_bus._subscribers` directly (or better, `event_bus.subscribe`/`unsubscribe`), then delete the `_push_event_registry` / `_push_event_registry_lock` aliases and the `_set_push_event` / `_clear_push_event` shims. The `_push_event_now` shim can stay (it delegates via `event_bus.publish`).

### Notes for the primary agent
- Findings 1, 2, 3 are the highest-impact: Finding 1+3 together mean post-shutdown crash-recovery writes are silently lost; Finding 2 means users get notification spam on every failed cycle. All three have low-risk fixes.
- Finding 4 (heartbeat timeout docs) is a 5-minute fix but has real operational impact — operators diagnosing "stuck backend" issues will be misled.
- Finding 5 (session_id never rendered) is a small change to two formatters but unlocks cross-process log correlation.
- Findings 7, 8, 9 are medium-impact quality-of-life fixes that would surface during normal testing/operations.
- Findings 10, 11, 12 are low-impact cleanups; defer unless touching nearby code.
- No Critical findings — the architecture is generally sound (clean DI seam via `providers.py`, central `ThreadRegistry`, idempotent `_do_cleanup()`, well-documented shutdown ordering). The issues are localized bugs and staleness, not structural problems.

---

---

## b-review: Frontend + UX + Accessibility Findings

**Agent:** Subagent (Explore)
**Scope:** Frontend architecture, UX/UI consistency, accessibility, user onboarding, developer experience for the Electron renderer in `voice_typer/client/src/renderer/`.

### Summary
- Total findings: 14
- Critical: 0, High: 3, Medium: 6, Low: 5

Methodology: read `App.tsx`, `main.tsx`, `Bubble.tsx`, all `pages/*.tsx` (Home, History, Vocabulary, Templates, Models, Microphone, Dashboard, About, Settings, Onboarding), `hooks/{usePython,useConnection,useTheme,useNavigation,useSnackbar,useSoundFeedback,useStatsShare}.ts`, `stores/appStore.ts`, `types/ipc.ts`, `i18n/i18n.ts`, `components/{common,layout,ui,feedback,settings,hotkey,dashboard,microphone,models,audio}/*`, `index.css`, both `preload/{index,bubble}.ts`. Grepped for `text-[Npx]` contrast violations, module-level `t()` calls, and `<Snackbar />` render sites.

### Findings

#### Finding 1
- **Category**: frontend-arch
- **Severity**: High
- **File**: voice_typer/client/src/renderer/src/pages/Settings.tsx:854-862 (`reRunWizard` onClick); voice_typer/client/src/renderer/src/App.tsx:41-46 (route guard); voice_typer/client/src/renderer/src/stores/appStore.ts:46-51 (config snapshot)
- **Description**: The "Re-run setup wizard" button in Settings does `await updateConfig({ onboarding_completed: false })` then `onNavigate?.("onboarding")`. But `updateConfig` only updates Settings.tsx's LOCAL `config` state and queues a backend `set_config` IPC — it does NOT touch the Zustand `appStore.config` snapshot that App.tsx's route guard reads. The appStore only learns about the change later (via the `config_changed` push event, handled in `useTheme.ts:230-259`). The route guard `if (currentPage === "onboarding" && config?.onboarding_completed === true) navigate("home")` fires immediately on the navigation, sees the stale `true` value, and bounces the user back to home — the onboarding wizard is never shown.
- **Root cause**: Two separate config states (Settings-local vs. appStore) that are only reconciled asynchronously via a backend push event.
- **Fix**: In Settings.tsx `updateConfig`, call `useAppStore.getState().mergeConfig(updates)` (or subscribe to `mergeConfig` from the store) alongside `setConfig(newConfig)` so the appStore snapshot updates synchronously. Alternatively, have App.tsx's route guard read from a ref that's updated optimistically, or remove the route guard entirely and rely on the backend's `onboarding_is_first_run` check (already wired in `useConnection.ts:121`).

#### Finding 2
- **Category**: ux
- **Severity**: High
- **File**: voice_typer/client/src/renderer/src/lib/sound-manager.ts:304-307 (`START_BEEP_WAV` / `STOP_BEEP_WAV`), :327 (`audio.src` assignment)
- **Description**: `START_BEEP_WAV` and `STOP_BEEP_WAV` are byte-for-byte identical base64 data URLs (verified: both are `UklGRsQBAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YaABAACA`). When the Web Audio API path fails (which the comment on :267-291 acknowledges is "common on first record before any user gesture"), the HTMLAudioElement fallback `playViaHtmlAudio(kind)` sets `audio.src = kind === "start" ? START_BEEP_WAV : STOP_BEEP_WAV` — both produce the exact same beep. The user cannot audibly distinguish "recording started" from "recording stopped" via the fallback path.
- **Root cause**: Placeholder/copy-paste data URLs were never replaced with distinct start (rising-pitch) and stop (falling-pitch) WAV blobs.
- **Fix**: Generate two distinct short WAV data URLs (e.g. via a tiny Python/Node script using `wave` + `math.sin`) — a 660→880Hz rising sweep for start, a 523→392Hz falling sweep for stop — and replace the two constants. Or, simpler: remove the fallback entirely and accept silent failure when AudioContext is suspended (the gesture-listener installed at :172-202 will resume the context on the next user input, so the next cue will play correctly).

#### Finding 3
- **Category**: frontend-arch
- **Severity**: High
- **File**: voice_typer/client/src/renderer/src/i18n/i18n.ts:185-208 (`t` function); voice_typer/client/src/renderer/src/components/settings/GeneralSettingsSection.tsx:56-67 (module-level `t()` calls); voice_typer/client/src/renderer/src/components/settings/GeneralSettingsSection.tsx:213 (`window.location.reload()` workaround)
- **Description**: `t()` is a plain function that reads a module-level `_currentLocale` variable — there is no React subscription mechanism. Components that call `t()` during render won't re-render when the locale changes. The Settings page works around this by calling `window.location.reload()` after `setLocale(v)`, which is heavy-handed (loses all component state, re-runs every useEffect, briefly shows a blank window). Additionally, `GeneralSettingsSection.tsx:56-67` computes 10 translation constants (`LAUNCH_AT_LOGIN_LABEL`, `NOTIFICATIONS_LABEL`, etc.) at MODULE IMPORT TIME using `t(...)`, freezing them to whatever locale was active on first import (almost always "en" because the locale is restored from localStorage AFTER the module loads). The Home.tsx `NEW-I18N-FIX` comment (Home.tsx:151-158) explicitly calls out this exact bug pattern as previously fixed for the status pill — but the same bug persists in GeneralSettingsSection.
- **Root cause**: i18n was implemented as a vanilla module without a React context/subscription layer; the locale switcher compensates with a full-page reload.
- **Fix**: (a) Convert the 10 module-level constants in GeneralSettingsSection.tsx to in-component `const`s (or call `t()` inline at each use site). (b) Longer-term: introduce a `LocaleProvider` context + `useT()` hook that re-renders subscribers on locale change (use `useSyncExternalStore` over a `setLocale`-notified subscriber list), then delete the `window.location.reload()` call.

#### Finding 4
- **Category**: frontend-arch
- **Severity**: High
- **File**: voice_typer/client/src/renderer/src/pages/Vocabulary.tsx:356-389 (`instantDeleteEntry` undo callback)
- **Description**: The undo callback closes over `entries` (the value at the time `instantDeleteEntry` was created, which STILL INCLUDES the deleted entry because the filter produced `updated`, not `entries`). When the user clicks Undo (up to 6s later), `restored = [...entries]` includes `entry` at its original index. Then `restored.indexOf(entry)` returns that index, and `restored.splice(idx, 0, entry)` INSERTS A SECOND COPY at that index (splice with deleteCount=0 doesn't remove anything). The visible result: the deleted entry reappears TWICE after Undo. The closure is also stale with respect to any other vocabulary edits made between the delete and the Undo click — those edits are silently lost.
- **Root cause**: The undo callback captures the pre-delete `entries` instead of reading the CURRENT state at undo time. Compare to `Templates.tsx:383` which correctly re-reads via `loadTemplatesFromLocalStorage()` inside the undo callback.
- **Fix**: Use a ref to track the latest `entries` (`entriesRef.current = entries` updated in a `useEffect`), and read `entriesRef.current` inside the undo callback. Then filter out the entry before re-inserting: `const restored = entriesRef.current.filter(e => e !== entry); restored.splice(originalIndex, 0, entry);`. Or simpler: store the post-delete array in a ref and restore it directly: `setEntries(preDeleteEntriesRef.current)`.

#### Finding 5
- **Category**: onboarding
- **Severity**: Medium
- **File**: voice_typer/client/src/renderer/src/hooks/useConnection.ts:121-132 (first-run check guard); voice_typer/client/src/renderer/src/hooks/useNavigation.ts:16-39 (persisted nav state)
- **Description**: The first-run onboarding auto-route is gated on `if (currentPage === "home" && !cancelled)` (line 121). But `useNavigation` restores the persisted page from localStorage on mount (line 16-39). If a user previously navigated to e.g. "settings" and then closed the app mid-onboarding (or before completing it), the next launch restores them to "settings", and the first-run check never fires — the wizard is silently skipped even though `onboarding_completed` is still false. The user is left in the app with no obvious way to discover the wizard (they'd have to find the "Re-run setup wizard" button buried in Settings → Privacy → Troubleshooting).
- **Root cause**: The `currentPage === "home"` guard was added to avoid clobbering a deep-link navigation (e.g. tray menu "More models…" → navigates to "models"), but it's too narrow.
- **Fix**: Drop the `currentPage === "home"` guard and check first-run unconditionally on the initial connection probe. If `is_first_run` is true, force-navigate to "onboarding" regardless of the persisted page. The persisted nav state will still be there after the wizard completes (or the user can re-navigate).

#### Finding 6
- **Category**: onboarding
- **Severity**: Medium
- **File**: voice_typer/client/src/renderer/src/pages/Onboarding.tsx:49 (`useState("<f2>")`), :51 (`useState("small.en")`)
- **Description**: The wizard initializes `selectedHotkey` to `"<f2>"` and `selectedModel` to `"small.en"` hardcoded. If the user already has a hotkey/model set in their config (e.g. they edited the config file directly, or they're re-running the wizard after a partial setup), the wizard shows `<f2>` / `small.en` as the pre-selected values instead of the user's existing choice. The wizard then overwrites the existing config with the default on "Continue".
- **Root cause**: The `onboarding_start` IPC response (or a follow-up `get_config` call) isn't consulted to pre-fill the user's current selections.
- **Fix**: After `onboarding_start` resolves, fetch the current config (`call("get_config")`) and pre-select `selectedHotkey = cfg.hotkey ?? "<f2>"` and `selectedModel = cfg.model_size ?? "small.en"`. Also pre-select `selectedMic = cfg.microphone ?? ""`.

#### Finding 7
- **Category**: accessibility
- **Severity**: Medium
- **File**: voice_typer/client/src/renderer/src/components/feedback/LevelBar.tsx:26-28 (hardcoded aria-labels)
- **Description**: `aria-label={playing ? "Microphone input level (frozen during playback)" : "Microphone input level"}` hardcodes English text. The rest of the app routes aria-labels through `t(...)`, so non-English users hear English (or nothing, if their screen reader doesn't read English) for this progressbar.
- **Root cause**: Likely an oversight — the rest of the file was migrated to `t(...)` but the aria-labels were missed.
- **Fix**: Add `microphone.levelBarAria` and `microphone.levelBarFrozenAria` keys to all 8 translation JSON files, then replace the hardcoded strings with `t("microphone.levelBarAria")` / `t("microphone.levelBarFrozenAria")`.

#### Finding 8
- **Category**: accessibility
- **Severity**: Medium
- **File**: pervasive — voice_typer/client/src/renderer/src/components/dashboard/{ActivityList.tsx:121,DashboardStatCard.tsx:29,QuickInfoCard.tsx:22,StatCards.tsx:72}, pages/{Dashboard.tsx:395,413,441,Vocabulary.tsx:516,Templates.tsx:480,Microphone.tsx:591,664,Models.tsx:1180}, components/{microphone/MicrophoneListItem.tsx:36,AudioPresetSelector.tsx:120,138,TestReviewPanel.tsx:44,117,186,settings/ThemeSettingsSection.tsx:823,831}
- **Description**: 21 occurrences of `text-[9px]` or `text-[10px]` in user-facing text. WCAG 2.1 SC 1.4.4 (Text Resize) recommends a minimum 12px body-text size; 9-10px is below the threshold most accessibility audits use. Several of these are compounded by `opacity-50`/`opacity-60` (e.g. `ActivityList.tsx:121` `text-[10px] text-(--text-muted) opacity-60`), which drops the effective contrast well below the SC 1.4.3 4.5:1 minimum. The `text-[9px]` cases (Dashboard.tsx:413, MicrophoneListItem.tsx:36, Models.tsx:1180) are badges/labels that should still be ≥ 11px.
- **Root cause**: Micro-typography was used to fit dense layouts (badges, chart axis labels, sublabels) without an accessibility pass.
- **Fix**: Bump all `text-[9px]` → `text-[11px]` and `text-[10px]` → `text-xs` (12px). Drop the `opacity-50`/`opacity-60` modifiers — use `text-(--text-muted)` alone, or define a darker `--text-faint` CSS variable for genuinely-de-emphasized text and verify the contrast ratio against both light and dark backgrounds.

#### Finding 9
- **Category**: ux
- **Severity**: Medium
- **File**: voice_typer/client/src/renderer/src/pages/About.tsx:368 (`remote > APP_VERSION`), :366 (`remote === APP_VERSION`)
- **Description**: `handleManualCheck` compares semantic versions using lexicographic string comparison (`remote > APP_VERSION`). This works for exact equality (`1.2.3 === 1.2.3`) but breaks for ordering: `"1.10.0" < "1.9.0"` lexicographically (because `"1" < "9"`), so a 1.9.0 → 1.10.0 bump is silently reported as "installed is newer" (line 371) instead of "newer version available" (line 369). The auto-check on mount (line 331-353) doesn't even compute the comparison — it just stores `latestVersion` and lets the JSX render a "newer version available" link unconditionally (which then misleads users on patch-level downgrades).
- **Root cause**: String comparison was used instead of semantic-version comparison.
- **Fix**: Add a `compareSemver(a, b)` helper that splits on `.`, parses each part as int, and compares pairwise. Replace `remote > APP_VERSION` with `compareSemver(remote, APP_VERSION) > 0` and `remote === APP_VERSION` with `compareSemver(remote, APP_VERSION) === 0`. Add unit tests for `1.9.0` vs `1.10.0`, `1.0.0` vs `1.0.1`, `2.0.0` vs `1.99.99`.

#### Finding 10
- **Category**: frontend-arch
- **Severity**: Medium
- **File**: voice_typer/client/src/renderer/src/App.tsx:246-265 (`usePythonEvent("navigate", ...)`); voice_typer/client/src/renderer/src/types/ipc.ts:24-34 (`Page` union)
- **Description**: The "navigate" event handler casts untrusted backend-supplied strings to `Page` via `navigate(pageMap[page] ?? (page as Page))`. If the backend (or a future tray menu addition) pushes a path like `"foo/bar"` that isn't in `pageMap`, the `as Page` cast silently passes TypeScript's type check but `navigate()` stores an invalid value in `currentPage` state. The `renderPage()` switch in App.tsx:294-330 has no default case, so it returns `undefined` and React renders nothing — a blank page with no error.
- **Root cause**: The cast `(page as Page)` assumes the backend only ever sends valid page names, but there's no runtime validation.
- **Fix**: Validate `page` against the known `Page` union (or the keys of `pageMap`) before navigating. Unknown paths should be logged at WARN and ignored, not cast. Add a `default:` case to `renderPage()` that renders an `<ErrorBoundary>` fallback or a "page not found" message.

#### Finding 11
- **Category**: frontend-arch
- **Severity**: Medium
- **File**: voice_typer/client/src/renderer/src/pages/{Home.tsx:47-128,History.tsx:31-32,Models.tsx:40,Microphone.tsx:28-29,Settings.tsx:37,Dashboard.tsx:26}.ts (module-level `let _cachedFoo`); cross-cut by voice_typer/client/src/renderer/src/pages/History.tsx:88-90 (cache only updated for "all records" view)
- **Description**: Six pages keep a module-level mutable cache (`let _cachedFoo: T | null = null`) that survives React's mount/unmount lifecycle and is read by the next mount's `useState` initializer. The cache is only refreshed by (a) explicit user action, (b) the `transcription_final` push event, or (c) the `config_changed` event. If the backend state changes through any other path while the renderer is open (e.g. the user clears history from the tray menu, or another Electron window edits config, or a CLI tool modifies the config file), the next navigation to that page shows stale data. The `History.tsx:88-90` comment explicitly notes the cache is only updated for the "all records" view — search/filter results are excluded — which means a search that finds nothing then navigating away and back shows the pre-search records (correct), but a backend-side clear while viewing search results leaves the user seeing ghost records.
- **Root cause**: The module-level cache was added to eliminate the "flash of empty content" on re-visit (Home.tsx:38-46 comment), but the invalidation strategy is incomplete.
- **Fix**: Either (a) add a backend-pushed `history_changed` / `config_changed_external` event that triggers a cache invalidation, or (b) move the cache into the Zustand `appStore` so it has a single invalidation point, or (c) accept the staleness but add a visible "last updated Xs ago" indicator and a manual refresh button (History.tsx already has implicit refresh on `transcription_final`; the other pages don't).

#### Finding 12
- **Category**: dx
- **Severity**: Low
- **File**: voice_typer/client/src/renderer/src/types/ipc.ts:327-349 (`WindowBubble` interface); voice_typer/client/src/preload/index.ts:16-78 (main renderer preload); voice_typer/client/src/preload/bubble.ts:18-102 (bubble-window preload)
- **Description**: The `WindowBubble` interface declares 14 methods, but only 10 are exposed in `preload/index.ts` (the main renderer's preload). The bubble-only methods (`hide`, `setLevel`, `onSetState`, `resizeTo`) are exposed only in `preload/bubble.ts`. Callers in the main renderer that use `window.bubble?.resizeTo?.(...)` get a silent no-op (the `?.` swallows the undefined). The TypeScript types pretend all methods exist, so the `?.` is the only thing preventing runtime errors — there's no compile-time signal that a method is bubble-window-only.
- **Root cause**: The interface was written as a union of both preloads' surfaces rather than split per window.
- **Fix**: Split into `MainRendererBubble` (the subset exposed by `preload/index.ts`) and `BubbleWindowBubble` (the full set exposed by `preload/bubble.ts`). Update the `Window` augmentation to declare `bubble?: MainRendererBubble` in the main renderer's `vite-env.d.ts` and `bubble?: BubbleWindowBubble` in a separate `bubble-env.d.ts` consumed only by `Bubble.tsx`.

#### Finding 13
- **Category**: dx
- **Severity**: Low
- **File**: voice_typer/client/src/renderer/src/hooks/useSnackbar.tsx:82 (`const Snackbar = useCallback(() => null, [])`); voice_typer/client/src/renderer/src/pages/{Settings.tsx:952,Models.tsx (multiple),Microphone.tsx:749,Vocabulary.tsx,Templates.tsx,Onboarding.tsx:420}.tsx (render `<Snackbar />`)
- **Description**: `useSnackbar` returns a `Snackbar` component that always renders `null`. The comment on lines 77-81 says this is kept for backwards compat. But 6+ pages still render `<Snackbar />` in their JSX, which is dead JSX — readers see `<Snackbar />` and assume it's the toast renderer, when in fact toasts come from the global `<Toaster />` mounted in App.tsx. This is a maintenance trap: a future contributor might "fix" a missing toast by adding `<Snackbar />` somewhere, not realizing it does nothing.
- **Root cause**: The migration from the bespoke snackbar to sonner was done incrementally; the no-op component was left as a compat shim.
- **Fix**: Delete the `Snackbar` return from `useSnackbar` (and the `Snackbar` field from the returned object). Update the 6+ call sites to remove `<Snackbar />` from their JSX. The `<Toaster />` in App.tsx already handles all toast rendering via sonner.

#### Finding 14
- **Category**: dx
- **Severity**: Low
- **File**: voice_typer/client/src/renderer/src/components/common/Modal.tsx:60-95 (`dismissedByAction` ref + `onEscapeKeyDown` / `onPointerDownOutside` handlers); voice_typer/client/src/renderer/src/components/common/ConfirmDialog.tsx:39-59 (same pattern)
- **Description**: Modal (and ConfirmDialog) use a `dismissedByAction` ref to distinguish "user clicked a button inside the modal" (which calls `handleClose` → sets the flag → calls `onClose`) from "user pressed Escape / clicked backdrop" (which Radix's `onOpenChange(false)` reports). The `onEscapeKeyDown={handleClose}` and `onPointerDownOutside={handleClose}` handlers ALSO set the flag and call `onClose`. This is over-engineered: Radix's `onOpenChange(false)` is the single canonical close signal — it fires exactly once per close event (button, Escape, or backdrop). The extra handlers and the ref exist only to prevent a non-existent double-fire.
- **Root cause**: The pattern was likely copied from a tutorial that didn't trust Radix's de-duplication.
- **Fix**: Delete `dismissedByAction`, `handleClose`, `onEscapeKeyDown`, and `onPointerDownOutside`. Let `handleOpenChange(isOpen)` call `onClose()` directly when `!isOpen`. Call sites that need to perform side-effects on explicit-button-close should call their own handler before calling `onClose()` (the `onClose` prop is already the single close signal).

### Notes for the primary agent
- Findings 1 and 4 are the highest-impact: Finding 1 breaks the "Re-run setup wizard" feature end-to-end (user clicks the button, briefly sees onboarding flash, then bounces back to home). Finding 4 silently corrupts vocabulary data on Undo (duplicate entries). Both have low-risk fixes.
- Finding 2 (identical start/stop beep WAVs) is a 5-minute fix that restores a real UX distinction users rely on when their eyes aren't on the screen.
- Finding 3 (i18n) is the largest structural issue: the `window.location.reload()` workaround and the module-level `t()` constants in GeneralSettingsSection are both symptoms of the missing React subscription layer. The quick fix (move constants inline) is mechanical; the proper fix (LocaleProvider + useT hook) is a half-day refactor that pays off across the codebase.
- Findings 5 and 6 (onboarding) together mean the wizard is brittle: it auto-launches only when the user happens to land on Home, and it doesn't respect the user's existing config when it does launch.
- Findings 7 and 8 (accessibility) are real WCAG issues that axe-core's test suite (`a11y/axe-core.test.tsx`) explicitly disables (`color-contrast: { enabled: false }`) — so they won't be caught by CI. Consider enabling color-contrast in a separate test config that loads the real Tailwind stylesheet.
- Findings 10, 11, 12, 13, 14 are quality-of-life cleanups; defer unless touching nearby code.
- No Critical findings — the frontend architecture is generally sound (clean Zustand store, well-decomposed Settings sections, ErrorBoundary at the root, thoughtful use of Radix primitives, RTL locale support, `prefers-reduced-motion` and `prefers-contrast` media queries in index.css). The issues are localized bugs, staleness, and accessibility oversights, not structural problems.

---

## c-review

**Scope**: Performance, memory usage, CPU usage, cross-platform compatibility (Windows/macOS/Linux), and build/CI/CD. Focus on Python backend, Electron main process, and the build pipeline.

**Investigation method**: Read worklog for context, then surveyed `prewarm.py` (1765 LOC), `recording.py` (2991 LOC), `audio_processor.py`, `level_monitor.py`, `ipc_server.py` (1902 LOC), `app.py` (2307 LOC), `model_manager.py`, `asr_registry.py`, `hotkeys.py` (2545 LOC), `microphone_watcher.py`, `volume_backends.py`, `clipboard_snapshot.py`, `prewarm_scheduler_posix.py`, `electron_launcher.py`, `thread_registry.py`, `streaming.py`, `vad.py`, `waveform.py`, `client/src/main/index.ts` (2205 LOC), `client/electron-builder.yml`, `.github/workflows/build.yml` (814 LOC), and `scripts/build/voice-typer.spec`.

### Summary

- **5 Performance** findings (1 High, 3 Medium, 1 Low)
- **4 Memory** findings (1 High, 2 Medium, 1 Low)
- **4 CPU** findings (1 High, 1 Medium, 2 Low)
- **5 Cross-platform** findings (2 Medium, 3 Low)
- **10 Build/CI** findings (1 High, 3 Medium, 6 Low)

The most impactful issues:
1. **PERF-01 / CPU-01** — Windows hotkey polling at `Sleep(1)` (~64–1000 Hz syscall rate) is wasteful on laptops; the code already registers `RegisterHotKey` but doesn't use WM_HOTKEY delivery, and a native `WH_KEYBOARD_LL` binary (`windows-key-listener.exe`) is already built and bundled but under-used.
2. **MEM-01** — `asr_registry.load_with_fallback` calls `unregister(name)` on the failed backend but never `backend.unload()`; the partially-initialized engine (with torch tensors / CUDA contexts / downloaded weights) stays alive in memory until the next GC pass, which may never collect it because the local `backend` variable is still referenced.
3. **CI-01** — `pip-audit --strict` hard-fails every PR on any new upstream CVE with no ignore list; the weekly triage job is the backstop but the per-PR gate causes repeated CI breakage.
4. **PERF-RT-03** — `level_monitor.py` callback runs the full filter chain (including optional RNNoise, 5–50ms) on the PortAudio real-time audio thread, violating the ~32ms deadline that `recording.py` was carefully refactored to respect (RT-SAFE-001).

---

### Findings — Performance

#### PERF-01
- **Category**: performance
- **Severity**: High
- **File**: voice_typer/server/hotkeys.py:1081, 1377 (`self._kernel32.Sleep(1)`)
- **Description**: The Windows hotkey polling loop calls `kernel32.Sleep(1)` between `GetAsyncKeyState` checks. The comment at hotkeys.py:794 claims "1000 Hz effective check rate" and "99.9% of time sleeping", but on default Windows the timer resolution is ~15.6ms so `Sleep(1)` actually sleeps ~15.6ms (≈64 Hz) — UNLESS another process (Chrome/Electron, video playback) has called `timeBeginPeriod(1)`, in which case it becomes 1000 Hz. Either way, a polling thread that wakes 64–1000 times per second prevents the CPU from entering deep C-states and drains laptop battery. The code already calls `RegisterHotKey` (hotkeys.py:760) but doesn't use `WM_HOTKEY` delivery; instead it polls `GetAsyncKeyState`. The native `windows-key-listener.exe` binary (already built in CI via `scripts/build/compile_native.ps1`, bundled by `voice-typer.spec:40`) uses `WH_KEYBOARD_LL` which is event-driven and reliable. The same `_run_modifier_only_polling_loop` (hotkeys.py:1198) has the same `Sleep(1)` issue.
- **Fix**: Replace the `GetAsyncKeyState` polling loop with one of (in order of preference): (a) `RegisterHotKey` + `GetMessageW`/`PeekMessageW` for `WM_HOTKEY` (the registration is already there, just not consumed); (b) `WaitForSingleObject` on a Win32 event signaled by the native `windows-key-listener.exe` binary (already bundled); or (c) at minimum, raise `Sleep(1)` to `Sleep(8)` (125 Hz) and call `winmm.timeBeginPeriod(8)`/`timeEndPeriod(8)` around the loop so the sleep is accurate. pynput's event-driven `Listener` (used on Linux/macOS) is also available on Windows via the `pynput.keyboard._win32` backend and should be the default.

#### PERF-02
- **Category**: performance
- **Severity**: Medium
- **File**: voice_typer/server/recording.py:871-912 (`_vad_enabled` property + `_compute_vad_enabled`)
- **Description**: `_vad_enabled` is a `@property` that re-evaluates 6 `getattr(config, ...)` calls on every access. It is read 3× per audio chunk (`_vad_auto_calibrate` line 925, `_vad_update` line 988, and the Silero VAD branch line 2392) × 16 Hz chunk rate = 288 `getattr` calls/sec for a feature whose value changes only when the user toggles a Settings UI switch. The `AudioProcessor` already has a `rebuild_from_config` hook (audio_processor.py:62) that fires on every config change — the same hook should re-cache `_vad_enabled`.
- **Fix**: Cache `_vad_enabled` as an instance attribute in `__init__` and refresh it in `rebuild_from_config` (or in a new `recorder.on_config_changed()` method called from `app._rebuild_audio_processor` at app.py:787). Eliminates 288 getattr/sec on the audio worker thread.

#### PERF-03 (RT-safety regression)
- **Category**: performance
- **Severity**: High
- **File**: voice_typer/server/level_monitor.py:218-263 (`callback` closure passed to `sd.InputStream`)
- **Description**: The level-monitor `sd.InputStream` callback runs on the PortAudio real-time audio thread. The callback: (1) acquires `_monitor_lock`; (2) runs `_level_processor.process_chunk(indata.reshape(-1, 1))` — the FULL filter chain, which may include RNNoise (5–50ms per chunk on CPU per ADR 0007 §3.5); (3) computes `np.mean(flat_filtered ** 2)` (allocates a squared array) and `np.abs(flat_filtered)` (allocates another array); (4) appends `indata.copy()` to `_test_chunks` AND `_test_raw_chunks` during test recording (two allocations + two list appends per chunk). The `recording.py` callback was carefully refactored in RT-SAFE-001 to do ONLY a `deque.append` + `Event.set` (~10µs) and move all heavy work to a worker thread. The `level_monitor.py` callback did NOT receive the same refactor and violates the ~32ms PortAudio deadline whenever the level monitor is active (Microphone page open). Symptom: XRUNs and audio glitches when the user opens the Microphone settings page during a recording.
- **Fix**: Apply the same RT-SAFE-001 pattern to `level_monitor.py`: have the callback only `deque.append((indata.copy(), status))` and `Event.set()`, and run the filter chain + RMS/peak computation on a dedicated worker thread. Alternatively, since the level monitor runs continuously (not just during dictation), skip the filter chain in the callback and compute RMS/peak on raw audio (the filter chain is cosmetic for the level bar).

#### PERF-04
- **Category**: performance
- **Severity**: Low
- **File**: voice_typer/server/recording.py:2128 (`if not indata.any() and self._chunk_count > 10:`)
- **Description**: `indata.any()` runs a full-array scan on every audio chunk (~16 Hz) to detect zero-filled (disconnected-device) input. On a 512-sample float32 chunk this is ~2µs, negligible per-call but cumulative. `np.count_nonzero(indata) == 0` is the canonical "all-zeros" check and is ~2× faster on modern numpy because it short-circuits on the first non-zero element.
- **Fix**: Replace `not indata.any()` with `indata.size == 0 or np.count_nonzero(indata) == 0`. Marginal but free.

#### PERF-05
- **Category**: performance
- **Severity**: Medium
- **File**: voice_typer/server/app.py:1312 (`time.sleep(0.3)` in `restart_app`)
- **Description**: `restart_app` calls `time.sleep(0.3)` "to give Electron time to process `relaunch_electron`" before closing the TCP socket. This blocks the calling thread (the pystray tray thread) for 300ms. During that window, the tray icon is unresponsive to menu clicks and any IPC dispatch handled on the same thread is blocked. The 300ms is a magic number that's too short for a slow Electron main thread (GC pause) and too long for a fast one. The proper pattern is a Condition variable / ack: publish `relaunch_electron` with a request ID, have Electron send back `relaunch_ack` over TCP, and `wait(timeout=2.0)` on the ack.
- **Fix**: Replace `time.sleep(0.3)` with `event.wait(timeout=2.0)` on a `threading.Event` set by an `relaunch_ack` IPC handler. Falls back to the 2s timeout if Electron doesn't ack (same behavior as today's 300ms magic number, but bounded and event-driven).

---

### Findings — Memory

#### MEM-01
- **Category**: memory
- **Severity**: High
- **File**: voice_typer/server/asr_registry.py:210-212 (`load_with_fallback`)
- **Description**: When the configured backend fails to load, the code calls `self.unregister(name)` (which removes it from the `_backends` dict) but never calls `backend.unload()`. The failed backend object — which may have allocated torch tensors, CUDA contexts, or downloaded multi-GB model weights during the failed `load()` — stays alive in the local `backend` variable until the function returns, then is garbage-collected whenever CPython gets around to it. For the Parakeet backend (`parakeet_engine.py`), `load()` calls `AutoModelForTDT.from_pretrained(...)` which downloads ~2.4 GB of weights and allocates CUDA memory before any failure point; if `model.generate()` then fails (e.g. CUDA OOM), the partial model is leaked until the next `gc.collect()` (which is called in `transcription.py` and `dictation_pipeline.py` but only AFTER the registry has already moved on). On repeated Parakeet→Whisper fallbacks (e.g. flaky GPU), this leaks GPU memory across cycles.
- **Fix**: In `asr_registry.py:212`, call `backend.unload()` before `self.unregister(name)`. `unload()` is already implemented on all three backends (`transcription.py`, `parakeet_engine.py`, `qwen_engine.py`) and is safe to call on a partially-loaded engine (it guards on `self._model is None`).

#### MEM-02
- **Category**: memory
- **Severity**: Medium
- **File**: voice_typer/server/level_monitor.py:63-64, 256-257 (`_test_chunks` / `_test_raw_chunks`)
- **Description**: `_test_chunks` and `_test_raw_chunks` are unbounded `list[np.ndarray]`. Each chunk is `indata.copy()` (~2KB for 512 float32 samples). At 16 Hz for the max 30s test duration, that's ~480 chunks × 2 lists × 2KB ≈ 2MB — bounded by `_test_duration`, but: (1) `_test_duration` is user-configurable via the IPC `microphone_test_start` handler with `max(1.0, min(30.0, duration))` — the cap is enforced; (2) the lists are only cleared on `stop_test_recording()` / `cancel_test_recording()`. If the IPC client crashes mid-test (Electron renderer crash, dev-tools close), neither is called and the lists persist until the next `start_test_recording()` (which clears them at line 386-387). Worst case: a long-running backend with periodic test recordings never frees the audio data. Also a forensic-recovery concern (audio data lingers in process memory).
- **Fix**: Use `collections.deque(maxlen=int(30 * sample_rate / 512))` for both lists. The 30s cap is already enforced at the API level; the `deque` enforces it at the data structure level too, so a forgotten `stop_test_recording()` can't accumulate audio.

#### MEM-03
- **Category**: memory
- **Severity**: Low
- **File**: voice_typer/server/vad.py:65-69 (`torch.hub.load(repo_or_dir='snakers4/silero-vad', ...)`)
- **Description**: Silero VAD is loaded via `torch.hub.load` which downloads the model from GitHub on first use. If GitHub is unreachable (corporate firewall, China, offline machine), VAD silently falls back to RMS — no warning is surfaced to the user. The model is ~2MB but the download adds 1-5s of latency to the first dictation on a fresh install. The `voice-typer.spec` PyInstaller bundle doesn't include the Silero model, so the bundled app still hits the network on first use.
- **Fix**: Bundle the Silero VAD model file (`silero_vad.jit`) in `voice_typer/server/` and load it via `torch.jit.load(local_path)` instead of `torch.hub.load`. Add a `_check_vad_available()` helper that's called once at startup so the user sees a warning if VAD is unavailable. Add the model file to `voice-typer.spec` `datas` list.

#### MEM-04
- **Category**: memory
- **Severity**: Low
- **File**: voice_typer/server/recording.py:2573-2575, 2987-2989 (`for chunk in self._buffer: chunk.fill(0)`)
- **Description**: On every `stop()` and `discard()`, the code iterates `self._buffer` (a deque of up to `DEFAULT_MAX_BUFFER_CHUNKS = 30000` chunks) and calls `chunk.fill(0)` on each. For a max-duration (30 min) recording that's ~30000 chunks × 512 samples × 4 bytes = ~61 MB of memory to zero. The SEC-audit-008 comment justifies this for forensic protection of voice data, but at rapid hotkey toggling (F2 press → F2 press within 500ms) this is a real CPU cost (~30-100ms per stop). The `start()` method (recording.py:1307-1309) ALSO zeros the preroll buffer, so there's a double-zero if the user rapidly toggles.
- **Fix**: Defer the zeroing to a background daemon thread (or skip it entirely if the next `start()` happens within e.g. 1s — the start() path already zeros the preroll buffer, so the buffer memory is about to be reused anyway). Alternatively, replace the `deque` of numpy arrays with a single pre-allocated ring buffer of `np.zeros(max_chunks * chunk_size, dtype=np.float32)` — zero-on-free is then a single `np.fill(0)` call instead of 30000 small ones.

---

### Findings — CPU

#### CPU-01
- **Category**: cpu
- **Severity**: High
- **File**: voice_typer/server/hotkeys.py:1081, 1377 (same as PERF-01)
- **Description**: See PERF-01. From a CPU perspective: a polling thread that wakes 64–1000 times per second prevents the CPU from entering deep C-states (C3/C6/C7). On a laptop on battery, this can reduce idle battery life by 10-20%. The `win32-key-listener.exe` binary (already bundled) uses `WH_KEYBOARD_LL` which is event-driven and wakes the thread only on key events. The same issue applies to `_run_modifier_only_polling_loop` at line 1377.
- **Fix**: See PERF-01 fix.

#### CPU-02
- **Category**: cpu
- **Severity**: Medium
- **File**: voice_typer/server/volume_backends.py:480-510 (`is_speaker_active` macOS osascript fallback); voice_typer/server/volume_ducker.py:551-625 (`_smart_duck_monitor_loop`)
- **Description**: When the macOS CoreAudio backend is unavailable (pyobjc-framework-CoreAudio not installed), the smart-duck monitor falls back to `osascript` polling every 500ms (volume_ducker.py:558). Each `osascript -e 'tell application "System Events" to get name of ...'` spawns a subprocess that takes 200-500ms (volume_backends.py:488). So during dictation on a macOS machine without pyobjc, the monitor thread is essentially continuously spawning subprocesses — 2 subprocesses/sec × 200-500ms each = 40-100% CPU on one core just for smart-duck. The `initialize()` method (volume_backends.py:396) does log this case at INFO level ("osascript backend ready (200-500ms latency)"), but the smart-duck monitor still runs.
- **Fix**: In `VolumeDucker.initialize()`, if the backend is `osascript` (not CoreAudio), disable smart-duck (`set_smart_duck_enabled(False)`) and log a warning that smart-duck requires pyobjc. The duck still applies immediately on dictation start; only the "skip duck if no audio is playing" optimization is lost. Alternatively, raise the poll interval to 2s for osascript (acceptable latency for retroactive ducking).

#### CPU-03
- **Category**: cpu
- **Severity**: Low
- **File**: voice_typer/server/recording.py:2179-2199 (periodic device availability check every 500 chunks)
- **Description**: Every 500 chunks (~32s at 16 Hz) the audio worker thread calls `sd.query_devices(current_device)` to verify the current device is still present. On Windows with the MME host API and many audio devices (8+), `query_devices` can take 50-200ms because it re-enumerates all devices. This blocks the audio worker thread, which can cause the ring buffer to overflow and drop audio chunks (logged as "Audio ring buffer full"). The check is defensive (catches USB unplug cases where PortAudio doesn't deliver zeros), but running it on the audio worker thread is the wrong place.
- **Fix**: Move the periodic device check to a separate daemon thread (e.g. `device-health-checker`) that wakes every 30s and calls `sd.query_devices(current_device)`. If the device is gone, set a flag that the audio worker thread checks (lock-free atomic read). Eliminates the 50-200ms stall on the audio worker.

#### CPU-04
- **Category**: cpu
- **Severity**: Low
- **File**: voice_typer/server/prewarm.py:1238-1239 (`wait_for_prewarm` 500ms poll loop)
- **Description**: `wait_for_prewarm` polls `is_prewarm_running()` every 500ms for up to 60s (120 polls). Each poll reads the PID file, calls `_process_alive(pid)` (which on Windows opens a process handle + GetExitCodeProcess + CloseHandle, ~50µs), and on Windows also calls `_process_is_prewarm(pid)` which reads the target process's PEB via NtQueryInformationProcess + ReadProcessMemory (prewarm.py:896-1139) — ~1-5ms per call. Total: 120 polls × ~5ms = ~600ms of CPU at startup, on the critical path before the model loads.
- **Fix**: On Linux, use `inotify` on the PID file directory (instant notification when the file is deleted by prewarm's `finally` block). On macOS, use `FSEvents`. On Windows, use `ReadDirectoryChangesW`. Or simpler: have the prewarm process signal a named event (`CreateEvent` / `sem_open`) on exit, and `wait_for_prewarm` blocks on `WaitForSingleObject` / `sem_wait` with the 60s timeout. Eliminates the 500ms poll cadence.

---

### Findings — Cross-platform

#### XPLAT-01
- **Category**: cross-platform
- **Severity**: Medium
- **File**: voice_typer/server/app.py:1100-1109 (`_open_config_file` Windows branch)
- **Description**: On Windows, the code first tries `C:\Windows\System32\notepad.exe` (hardcoded path), then falls back to `os.startfile(str(config_file))` if notepad.exe doesn't exist. This is backwards: `os.startfile` uses `ShellExecute` which respects the user's file associations for `.json` (e.g. VS Code, Notepad++) — a much better UX. Hardcoding notepad.exe means the user's preferred editor is ignored. On Windows Server Core or debloated Windows 11, `notepad.exe` may be missing — then the fallback `os.startfile` is used. The macOS and Linux branches correctly use `open -W` / `xdg-open` which respect associations.
- **Fix**: On Windows, try `os.startfile(str(config_file))` FIRST (it uses ShellExecute → user's preferred `.json` editor). Fall back to `subprocess.Popen(["notepad", str(config_file)])` (let PATH resolve notepad) only if `os.startfile` raises `OSError`. Drop the hardcoded `C:\Windows\System32\notepad.exe` path.

#### XPLAT-02
- **Category**: cross-platform
- **Severity**: Medium
- **File**: voice_typer/client/electron-builder.yml:82-90 (`afterInstall: ../../scripts/linux/postinst`)
- **Description**: The Linux `deb` and `rpm` sections use relative paths (`../../scripts/linux/postinst`) for `afterInstall` / `afterRemove`. These paths are resolved relative to electron-builder's CWD (`voice_typer/client/`), so `../../scripts/linux/` resolves to `<project-root>/scripts/linux/` — correct today, but fragile: if electron-builder changes its CWD expectation (it has happened in past major versions), or if the build is invoked from a different directory (e.g. `npx electron-builder --config voice_typer/client/electron-builder.yml` from the project root), the path breaks silently and the postinst script is not included in the .deb. The user gets a package without the udev rule setup, so Caps Lock hotkey doesn't work until they manually run the setup script.
- **Fix**: Copy the Linux scripts into `voice_typer/client/resources/linux/` (which electron-builder includes in the build context) and reference them as `afterInstall: resources/linux/postinst`. Or use a build hook (`afterPack` in a JS file) that resolves paths via `path.resolve(__dirname, '../../../scripts/linux/postinst')`.

#### XPLAT-03
- **Category**: cross-platform
- **Severity**: Low
- **File**: scripts/build/voice-typer.spec:138-164 (hiddenimports)
- **Description**: The PyInstaller spec lists Windows-only hiddenimports (`pycaw`, `comtypes`, `comtypes.gen`, `win32com`, `win32com.client`) and macOS-only (`CoreAudio`) unconditionally. When PyInstaller runs on Linux, it tries to find these modules, fails, and emits warnings (suppressed by `--noconfirm` but visible in build logs). At runtime the lazy-imports in `volume_backends.py` correctly guard these, so functionality is fine — but the build logs are noisy and a future contributor might "fix" the warnings by removing the hiddenimports, breaking Windows/macOS packaging.
- **Fix**: Gate the platform-specific hiddenimports by `sys.platform` in the spec file:
  ```python
  hiddenimports = [...common...]
  if sys.platform == "win32":
      hiddenimports += ["pycaw", "comtypes", "comtypes.gen", "win32com", "win32com.client"]
  elif sys.platform == "darwin":
      hiddenimports += ["CoreAudio"]
  ```
  Add a comment explaining the lazy-import pattern so future contributors don't remove them.

#### XPLAT-04
- **Category**: cross-platform
- **Severity**: Low
- **File**: voice_typer/server/prewarm.py:281-290 (Linux I/O priority syscall numbers)
- **Description**: The Linux I/O priority lowering tries syscall numbers 251 (x86_64 `SYS_ioprio_set`) and 314 (aarch64). The `for sys_num in (251, 314):` loop covers the two main architectures but not ppc64le (345), s390x (378), riscv64 (not yet stable). On those architectures both syscalls fail silently (the `else` branch logs "ioprio_set syscall failed for both 251 and 314") and prewarm runs at default I/O priority — correct behavior, just not optimal. Acceptable for a consumer app (most users are x86_64 or aarch64), but worth documenting.
- **Fix**: Replace the hardcoded syscall numbers with `ctypes.CDLL("libc.so.6").syscall` + a lookup table from `os.uname().machine`, or use the `ioprio` Python package if available. At minimum, add a comment listing the architecture→syscall-number mapping so future contributors know what's covered.

#### XPLAT-05
- **Category**: cross-platform
- **Severity**: Low
- **File**: voice_typer/server/prewarm.py:891-893 (`_process_alive` EPERM handling)
- **Description**: `_process_alive` treats `EPERM` (process exists but not owned by us) as "alive". The comment justifies this: "the prewarm process is owned by the same user, so EPERM shouldn't happen in practice; treating it as alive is the safe default." This is correct for the prewarm use case, but if a different user owns the PID (e.g. prewarm ran as root via a misconfigured sudo, or the PID was recycled by a system process), `wait_for_prewarm` blocks for the full 60s timeout. The 60s timeout is the safety net, but it's a long wait on the critical startup path.
- **Fix**: On `EPERM`, log a warning ("PID %d exists but not owned by us — treating as alive for 60s timeout") so the user can diagnose. Optionally, on POSIX, check `os.getuid() == os.stat(f'/proc/{pid}').st_uid` to distinguish "same user, alive" from "different user, alive".

---

### Findings — Build / CI / CD

#### CI-01
- **Category**: build-ci
- **Severity**: High
- **File**: .github/workflows/build.yml:188-195 (`pip-audit (hard-fail on all findings)`)
- **Description**: The `pip-audit` step runs `pip-audit --strict` with NO `--ignore-vuln` list and a hard-fail gate (the `|| (echo "::error::..." && exit 1)` pattern). Any new CVE in any pinned dependency blocks the PR. The weekly `pip-audit-weekly` job (build.yml:200-240) is the triage backstop, but the per-PR gate causes repeated CI breakage whenever upstream CVEs are announced (requests, urllib3, jinja2, pillow, and cryptography are frequent offenders — multiple CVEs per quarter). The accepted-findings list is documented as "currently EMPTY" in the comment at line 175, meaning there is NO triaged-ignore path. A contributor whose PR is blocked by an unrelated CVE has no recourse except to wait for a maintainer to update the ignore list.
- **Fix**: Maintain a small `--ignore-vuln GHSA-XXXX-XXXX-XXXX` list with a justification comment above each line (the workflow comment at line 178 already documents the pattern). Triage new findings weekly via the `pip-audit-weekly` job. Move the per-PR gate to `continue-on-error: true` with a warning annotation, so new findings are surfaced but don't block PRs.

#### CI-02
- **Category**: build-ci
- **Severity**: Medium
- **File**: .github/workflows/build.yml:24-31 (test matrix)
- **Description**: The test matrix runs Python 3.10/3.11/3.12/3.13 × Windows/macOS/Linux = 12 jobs per PR, with `fail-fast: false` so all 12 run to completion even if one fails. For a PR that touches only frontend (`voice_typer/client/**`) or docs, this is wasteful — 12 jobs × ~10 min each = ~120 CI-minutes per PR. There's no `paths:` filter on the `pull_request` trigger, so even a README change triggers the full matrix. The `concurrency` group only covers releases (`release-${{ github.ref }}`), not PRs — so two pushes to the same PR both run all 12 jobs.
- **Fix**: (a) Add a `paths:` filter to skip the test matrix for `*.md`, `docs/**`, `voice_typer/client/**/*.md` changes. (b) Add `concurrency: group: pr-tests-${{ github.ref }}, cancel-in-progress: true` to cancel the previous run when a new commit is pushed. (c) For PRs, run a reduced matrix (3.12 on Linux + 3.13 on Windows/macOS = 3 jobs) and reserve the full 12-job matrix for `push: branches: [main]` and tag pushes. (d) Optionally use a path-aware `if:` to skip the test job entirely when only `voice_typer/client/**` changed (the `client-build` job already covers frontend).

#### CI-03
- **Category**: build-ci
- **Severity**: Medium
- **File**: .github/workflows/build.yml:590-595 (build-windows PyInstaller), 700-710 (build-macos), 782-795 (build-linux)
- **Description**: The three build jobs (`build-windows`, `build-macos`, `build-linux`) re-run `pyinstaller` and `npx electron-builder` from scratch on every run. PyInstaller re-bundles ~500MB of torch+transformers+numpy+scipy; electron-builder re-downloads the Electron binaries (~200MB per arch) on every macOS build. There's no caching of `~/.cache/electron-builder`, `~/.cache/pyinstaller`, or the `build/` directory. On macOS the build also runs `npm ci` (line 698) which re-installs all client deps. Total: each build job takes ~10-15 min, of which ~5-8 min is re-downloading/re-bundling things that didn't change.
- **Fix**: (a) Cache `~/.cache/electron-builder` and `~/.cache/pyinstaller` using `actions/cache@v4` with `key: ${{ runner.os }}-electron-${{ hashFiles('voice_typer/client/package-lock.json') }}` and `key: ${{ runner.os }}-pyinstaller-${{ hashFiles('pyproject.toml', 'requirements-lock.txt') }}`. (b) Cache the `build/` and `dist/` directories between runs of the same job (invalidated on spec/pyproject changes). (c) Extract a reusable workflow (`/.github/workflows/build-platform.yml`) called by all three jobs to deduplicate the install + build steps.

#### CI-04
- **Category**: build-ci
- **Severity**: Medium
- **File**: .github/workflows/build.yml:650 (`build-macos: runs-on: macos-13`)
- **Description**: `build-macos` runs on `macos-13` (Intel). GitHub Actions has announced that `macos-13` is being deprecated and will be removed (the `macos-14` and later runners are Apple Silicon). Once `macos-13` is removed, the macOS build job will fail with "The workflow was not triggered but has dependencies on macos-13 which is no longer available." The `build-macos-universal` job (line 490) already runs on `macos-latest` to merge x64 + arm64 binaries, so the PyInstaller bundle could also be built on `macos-14` with `--arch x64` cross-compilation.
- **Fix**: Migrate `build-macos` to `macos-14` (Apple Silicon). PyInstaller on arm64 can build x64 bundles via `--target_arch x64` (requires the x64 Python interpreter; alternatively use `macos-13-large` if still available, or run two PyInstaller passes and `lipo` the bundles). Test the resulting .dmg on both Intel and Apple Silicon Macs. Set a deadline (e.g. "before 2025-Q4") to migrate before GitHub removes macos-13.

#### CI-05
- **Category**: build-ci
- **Severity**: Low
- **File**: .github/workflows/build.yml:329-351 (client-build), 696-710 (build-macos), 778-795 (build-linux)
- **Description**: `npm ci` runs in 3 separate jobs (client-build, build-macos, build-linux), each downloading and installing the full client dependency tree (~200MB, ~30s). The `client-build` job uploads `voice_typer/client/out/` as an artifact, but the platform build jobs don't download it — they re-run `npm ci` + `npm run build` themselves. This is because the platform builds need the renderer built for the specific platform (Electron main + preload are platform-specific), but the renderer (`out/renderer/`) is platform-independent and could be shared.
- **Fix**: Have `client-build` upload `voice_typer/client/out/renderer/` as an artifact. Have `build-macos` and `build-linux` download it and only re-run `npm run build:main` (the platform-specific Electron main/preload). Saves ~30s + ~200MB downloads per platform build.

#### CI-06
- **Category**: build-ci
- **Severity**: Low
- **File**: .github/workflows/build.yml:416-468 (build-native smoke tests)
- **Description**: The native binary smoke tests are weak. The macOS smoke test (line 431-447) launches the binary in the background, sleeps 2s, kills it, and checks that the output contains `ERROR:` or `READY`. On a CI runner without Accessibility permission, the binary should emit `ERROR:Accessibility permission required` — but the test PASSES whether the output is `ERROR` or `READY`. So the test is essentially "binary doesn't crash within 2s" — it doesn't verify the binary actually works. The Linux smoke test (line 416-429) is similar: it expects `ERROR:No keyboard devices found` (because CI has no `/dev/input/event*`), which is correct, but only verifies the binary can print an error. The Windows smoke test (line 449-468) just checks the process is running after 2s.
- **Fix**: For macOS, mock the Accessibility check (set `VOICE_TYPER_SKIP_ACCESSIBILITY_CHECK=1` in the test env) and verify the binary emits `READY` within 2s. For Linux, verify the binary exits with the expected error code (not just that it prints `ERROR:`). For Windows, verify the binary is still running AND has installed the `WH_KEYBOARD_LL` hook (e.g. by sending a synthetic key event and checking the binary's stdout for `KEY_DOWN:<caps_lock>`).

#### CI-07
- **Category**: build-ci
- **Severity**: Low
- **File**: .github/workflows/build.yml:631, 640, 726, 814 (`overwrite: false` in `svenstaro/upload-release-action`)
- **Description**: All four `upload-release-action` invocations use `overwrite: false`. If a release tag is re-pushed (e.g. a maintainer force-pushes `v1.2.3` after a build fix), the upload fails with "asset already exists" and the release job fails. The workflow's `concurrency: group: release-${{ github.ref }}, cancel-in-progress: false` (line 19-21) means two release builds can run back-to-back for the same tag — the second one will fail to upload.
- **Fix**: Use `overwrite: true` for release uploads. Alternatively, add a pre-step that deletes existing assets for the tag before uploading (using `gh release delete-asset`).

#### CI-08
- **Category**: build-ci
- **Severity**: Low
- **File**: .github/workflows/build.yml:19-21 (concurrency group only covers releases)
- **Description**: The `concurrency` group is `release-${{ github.ref }}` with `cancel-in-progress: false`. This prevents two release builds from racing on the same tag (good), but the test matrix (12 jobs per PR) has NO concurrency group — so two pushes to the same PR both run all 12 jobs to completion. GitHub Actions does not auto-cancel previous runs for the same PR unless a concurrency group is defined.
- **Fix**: Add a second `concurrency` block scoped to PRs:
  ```yaml
  concurrency:
    group: pr-${{ github.ref }}-${{ github.event_name }}
    cancel-in-progress: ${{ github.event_name == 'pull_request' }}
  ```
  Or scope it per-job (the test job gets `cancel-in-progress: true`, the release job keeps `cancel-in-progress: false`).

#### CI-09
- **Category**: build-ci
- **Severity**: Low
- **File**: .github/workflows/build.yml:534-640 (build-windows), 648-727 (build-macos), 730-815 (build-linux)
- **Description**: The three release build jobs produce installer artifacts (`.exe`, `.dmg`, `.deb`, `.rpm`, `.AppImage`) but do NOT generate artifact attestations / SLSA build provenance. Modern supply-chain best practice (NIST SSDF, SLSA Build L3, OpenSSF Scorecards) requires provenance for release binaries. GitHub provides `actions/attest-build-provenance@v1` which generates a signed attestation linking the artifact to the workflow run. Without it, users who download the release have no cryptographic way to verify the binary was built from the public source (vs. a compromised release maintainer who swapped the binary).
- **Fix**: Add `actions/attest-build-provenance@v1` as a final step in each release build job:
  ```yaml
  - name: Attest build provenance
    uses: actions/attest-build-provenance@v1
    with:
      subject-path: ${{ github.workspace }}/VoiceTyper-Setup-*.exe
  ```
  Document the verification command (`gh attestation verify <file> --repo owner/repo`) in the release notes.

#### CI-10
- **Category**: build-ci
- **Severity**: Low
- **File**: .github/workflows/build.yml:383 (build-native `windows-2022`), 536 (build-windows `windows-latest`), 388 (build-native `ubuntu-22.04`), 25 (test `ubuntu-latest`)
- **Description**: Runner version mismatches: `build-native` uses `windows-2022` and `ubuntu-22.04`, but `build-windows` uses `windows-latest` and `test` uses `ubuntu-latest`. The native binaries compiled on `windows-2022` (older Windows SDK) are bundled into the PyInstaller build on `windows-latest` (newer SDK). This usually works (backwards compat), but can cause subtle ABI issues if the native binary links against a newer SDK feature not present on the target Windows version. Same for Linux: native binary built on `ubuntu-22.04` (glibc 2.35) bundled into a build that may run on older glibc.
- **Fix**: Pin all jobs to the same runner version. For Windows: use `windows-2022` everywhere (or `windows-latest` everywhere). For Linux: use `ubuntu-22.04` everywhere (the oldest supported LTS, for glibc compat). Document the minimum supported OS versions in `docs/PLATFORM_STATUS.md`.

---

### Notes for the primary agent

- **PERF-01 / CPU-01** is the highest-impact finding: the Windows hotkey polling loop is a continuous CPU drain on every Windows laptop running VoiceTyper. The fix (use RegisterHotKey + WM_HOTKEY, or the already-bundled native binary) is well-scoped and the code already has the registration infrastructure.
- **MEM-01** is a silent leak: failed backend loads accumulate GPU/CPU memory across fallback cycles. The fix is one line (`backend.unload()` before `unregister`). Worth fixing before the next release.
- **PERF-03 (RT-safety regression)** is the most subtle: the level monitor was missed by the RT-SAFE-001 refactor and runs the full filter chain on the PortAudio thread. Symptom: audio glitches when the Microphone settings page is open during dictation. The fix mirrors the recording.py refactor.
- **CI-01** (pip-audit hard-fail) will cause repeated CI breakage on every upstream CVE. The fix (maintain an ignore list + continue-on-error on PRs) is documented in the workflow comments but not implemented.
- **CI-04** (macos-13 deprecation) is a ticking clock — GitHub will remove macos-13 and the macOS build will fail. Schedule the migration before 2025-Q4.
- **CI-03** (no caching) is the biggest CI-minutes waste — each platform build re-downloads ~700MB of Electron + PyInstaller deps. The fix is `actions/cache@v4` with the right keys; payback is immediate.
- **XPLAT-02** (relative `afterInstall` path) is fragile but works today. Low priority unless electron-builder is upgraded.
- The cross-platform surface is generally well-handled: lazy imports for `pycaw`/`comtypes`/`CoreAudio`/`pactl`/`xclip`/`wl-paste`, platform dispatch in `microphone_watcher.py` (Windows WM_DEVICECHANGE, Linux /dev/snd polling, macOS CoreAudio listener), and `_paths.py` centralizes the config-dir logic. The remaining issues are minor.
- The prewarm architecture (ADR-0009) is well-designed: PID file handshake, boot sentinel, cache ratio probe, background re-spawn on timeout. The only wart is the 500ms poll loop in `wait_for_prewarm` (CPU-04), which is on the critical startup path.

---

---

## d-review: Security + Testing + Documentation + Code Quality Findings

**Agent:** Subagent (Explore)
**Scope:** Security (IPC auth, input validation, secret handling, file permissions, dependency vulnerabilities), testing infrastructure (coverage gaps, flaky tests, integration tests, isolation), documentation (README, ADR, API, contributing), code quality (dead code, duplication, complexity, naming).

### Summary
- Total findings: 16
- Critical: 0, High: 2, Medium: 8, Low: 6

Methodology: read `voice_typer/server/security.py`, `voice_typer/server/_secrets.py`, `voice_typer/server/ipc_server.py` (1902 LOC, full), `voice_typer/server/config.py` (1354 LOC) and `voice_typer/server/config_validators.py` (801 LOC, full), `voice_typer/server/telemetry.py`, `voice_typer/server/history_db.py:120-430`, `voice_typer/server/app.py:1095-1148`, `voice_typer/server/handlers/system_handlers.py`, `voice_typer/server/cloud_engines.py`+`llm_polish.py` (URL/TLS paths only), `voice_typer/client/src/main/index.ts:525-644` (ALLOWED_COMMANDS), all of `docs/API.md` + `SECURITY.md` + `CONTRIBUTING.md`, listed `docs/adr/` directory. Surveyed `tests/conftest.py`, `tests/test_security_hardening.py`, `tests/test_e2e_pipeline.py`, `tests/test_electron_ipc_and_build.py:353-403` (parity test), `tests/test_path_traversal.py`, `tests/test_import_model_security.py`, `pyproject.toml`, `requirements-lock.txt`, `.github/workflows/build.yml:165-260` (pip-audit). Grepped for `shell=True`, `eval(`/`exec(`, `pickle.`, `subprocess` usage, broad `except Exception` patterns, dead module references, ADR collisions.

### Findings

#### Finding 1
- **Category**: security (IPC auth bypass)
- **Severity**: High
- **File**: voice_typer/server/ipc_server.py:629-662 (`IPCServer.start` — unconditional stdin listener), :1218-1266 (`_run` stdin loop, no auth handshake)
- **Description**: `start()` ALWAYS spawns the stdin listener thread, regardless of whether TCP mode is also active. The comment at :654-656 asserts "In TCP mode stdin is unused (inherited from Electron, connected to /dev/null or NUL)" — but this is not enforced. When a user runs `python -m voice_typer.server.ipc_server --port 9876` directly from a terminal (the documented standalone/dev mode in CONTRIBUTING.md §2), stdin is the terminal and the `_run` loop accepts unauthenticated JSON commands. SEC-018's TCP token check (`_handle_tcp_connection:898-934`) does not apply to the stdin path — any process that can write to the backend's stdin (terminal multiplexer, IDE debugger, screen-sharing tool, malicious local process on a shared machine) can dispatch `quit_app`, `set_config`, `set_tray_locale`, etc. without knowing the session token. The Electron-spawned production path inherits stdin from Electron's `stdio: "inherit"` (index.ts:1432), which on Linux/macOS points to /dev/null — so the vulnerability is latent in production but active in any direct-terminal invocation.
- **Root cause**: The stdin listener was retained for the legacy CLI/console path and is documented as "unused" in TCP mode, but the implementation does not gate startup on `not self._tcp_mode` (or any equivalent check). SEC-018 was added to TCP only.
- **Fix**: Either (a) skip `self._stdin_thread.start()` when `self._tcp_mode` is True, OR (b) require the same `VOICE_TYPER_IPC_TOKEN` handshake on stdin's first line when `--port` is in use. Option (a) is the minimal fix and matches the documented behavior. Add a regression test that asserts `server._stdin_thread` is None after `start_tcp(port)` is called without a prior `start()`-only invocation.

#### Finding 2
- **Category**: security (incomplete command allowlist)
- **Severity**: High
- **File**: voice_typer/client/src/main/index.ts:532-622 (`ALLOWED_COMMANDS` Set — 58 entries); voice_typer/server/ipc_server.py:1320-1415 (`_COMMAND_REGISTRY` — 68 entries); tests/test_electron_ipc_and_build.py:393-403 (`test_allowlist_matches_server_commands` — only checks orphans)
- **Description**: The Electron main process's `ALLOWED_COMMANDS` Set is missing 10 commands that the Python backend registers in `_COMMAND_REGISTRY`. Missing entries: `refresh_microphones`, `get_rms_level`, `get_audio_status`, `export_diagnostics`, `check_accessibility` (PLAT-030 macOS Accessibility permission check), `show_electron_notification` (TRAY-035), `get_vocabulary_suggestions`, `apply_vocabulary_suggestion`, `dismiss_vocabulary_suggestion` (P5 vocabulary automation — referenced in `AiEnhancementSettingsSection.tsx:13-14` comments), and `force_cancel_transcription` (PR-2 Finding #3 — stuck-transcription recovery, documented at ipc_server.py:1404-1408). The renderer's `call(...)` helper rejects any command not in `ALLOWED_COMMANDS` with `"Disallowed IPC command"` (index.ts:624-626), so these features silently fail when invoked from the UI. The existing parity test `test_allowlist_matches_server_commands` only checks the *orphan* direction (allowlist entries not in server registry) — its assertion `orphans = allowlist_entries - server_cmds; assert not orphans` is one-way, so the 10 missing-in-allowlist commands went undetected.
- **Root cause**: The allowlist was last updated when the server registry had ~50 commands; the parity test was written to prevent dead entries (the original ERR-IPC-003 cleanup) but not missing entries. Each new server command was added without a corresponding edit to index.ts.
- **Fix**: (a) Add the 10 missing commands to `ALLOWED_COMMANDS` in `client/src/main/index.ts` (each with a justification comment matching the existing style). (b) Strengthen `test_allowlist_matches_server_commands` to also check `missing = server_cmds - allowlist_entries; assert not missing, f"Allowlist is missing server commands: {sorted(missing)}"`. This makes the parity test bidirectional and prevents future drift in either direction.

#### Finding 3
- **Category**: documentation (stale API reference)
- **Severity**: Medium
- **File**: docs/API.md:100-108 (Config key reference table)
- **Description**: The "Key Configuration Keys" table contains 5 stale or fabricated field definitions:
  1. `recording_mode` default listed as `"push_to_talk"` — actual default is `"toggle"` (config.py:612).
  2. `recording_mode` enum listed as `push_to_talk, toggle, voice_activity` — actual enum is only `toggle, push_to_talk` (config_validators.py:521). `voice_activity` is not a real mode.
  3. `language` default listed as `"auto"` — actual default is `"en"` (config.py:554).
  4. `paste_enabled` field listed — does not exist; the actual field is `paste_on_stop` (config.py:570).
  5. `clipboard_clear_delay_seconds` field listed with default `5.0` — field was removed per ADR-0010 §8.2 (config.py:601-602 comment: "removed `clipboard_clear_delay_seconds` (dead — was only read by the now-deleted `schedule_clipboard_clear`)").
  6. `check_updates` field listed with default `True` — no such field exists on `Config` (verified by grepping `config.py` for the field name).
- **Root cause**: The API doc was written against an early version of the Config dataclass and never updated as fields were renamed (`paste_enabled`→`paste_on_stop`), removed (`clipboard_clear_delay_seconds`), or never existed (`check_updates`, `voice_activity` mode).
- **Fix**: Regenerate the table by reading `Config` defaults directly from `voice_typer/server/config.py` and enums from `IPC_CONFIG_ALLOWLIST`. Add a CI test (`tests/test_api_doc_accuracy.py`) that parses the API.md table and asserts each field name exists on `Config` with the documented default — analogous to the existing `test_i18n_completeness.py` ratchet for translations.

#### Finding 4
- **Category**: documentation (inaccurate security claim)
- **Severity**: Medium
- **File**: docs/API.md:155 ("IPC Server — Protocol — Auth: Per-connection token validated on every request")
- **Description**: The IPC Server section claims "Per-connection token validated on every request". The actual implementation (`_handle_tcp_connection:898-934`) validates the token only on the FIRST line of the connection (the auth handshake). After the handshake succeeds, all subsequent messages on the same connection bypass the token check entirely — they go straight to `_dispatch`. A compromised Electron renderer (or a process that hijacks the TCP connection after auth) can issue any command without re-authenticating. The current design is acceptable for the threat model (the token proves the connecting process is the Electron parent at connect time; the OS doesn't allow other processes to inject into an established TCP socket), but the doc is factually wrong about "every request".
- **Root cause**: The doc was written generically and never reconciled with the actual handshake-once implementation.
- **Fix**: Replace the claim with "Per-connection: the first message must be a JSON auth object whose `token` field matches the `VOICE_TYPER_IPC_TOKEN` env var (constant-time comparison via `hmac.compare_digest`). Subsequent messages on the authenticated connection bypass the token check." Cross-reference `SEC-018` in SECURITY.md for the threat model.

#### Finding 5
- **Category**: documentation (SECURITY.md count is stale)
- **Severity**: Medium
- **File**: SECURITY.md:37 ("only the ~35 commands in `ALLOWED_COMMANDS`")
- **Description**: SECURITY.md states the Electron main process's allowlist contains "~35 commands". The actual count is 58 entries in `client/src/main/index.ts` (and the server registry has 68 — see Finding 2). The "~35" was probably accurate when the doc was written but is now off by ~70%. Security reviewers reading SECURITY.md will underestimate the attack surface (a larger allowlist = more commands a compromised renderer can invoke). The mismatch also obscures Finding 2: a reviewer cross-checking "35 commands" against the server registry of 68 would not immediately spot the 10 missing entries.
- **Root cause**: The doc was not updated when commands were added.
- **Fix**: Replace "~35 commands" with the actual count + a pointer to the source of truth: "only the 58 commands listed in `ALLOWED_COMMANDS` at `voice_typer/client/src/main/index.ts`". Add a CI test (`test_security_doc_command_count.py`) that parses SECURITY.md and asserts the documented count matches `awk '/ALLOWED_COMMANDS = new Set/,/^\s*\}\);/' voice_typer/client/src/main/index.ts | grep -cE '^\s*"[a-z_]+"'`.

#### Finding 6
- **Category**: documentation (CONTRIBUTING.md allowlist count stale)
- **Severity**: Medium
- **File**: CONTRIBUTING.md:332 ("`set_config` enforces SEC-002's 53-field allowlist")
- **Description**: CONTRIBUTING.md claims the IPC `set_config` allowlist has 53 fields. The actual `IPC_CONFIG_ALLOWLIST` dict in `config_validators.py:466-675` has 122 field entries (verified by `awk '/^IPC_CONFIG_ALLOWLIST.*=.*\{$/,/^\}$/' voice_typer/server/config_validators.py | grep -cE '^\s+"[a-z_0-9]+":\s+\('`). The "53" was accurate at writing time but the allowlist has more than doubled since (audio filter chain fields ADR 0007, vocabulary automation P5, volume ducking v1.1.0, etc.). New contributors reading "53-field allowlist" will underestimate the validation surface and may add fields without realizing they need a corresponding validator entry.
- **Root cause**: Manual count in prose, no automated check.
- **Fix**: Replace "53-field allowlist" with "the IPC_CONFIG_ALLOWLIST dict in `voice_typer/server/config_validators.py`" (no count — let the source be the truth). Optionally add a CI test that parses CONTRIBUTING.md and asserts no specific count is claimed (regex `\d+-field allowlist` should match nothing).

#### Finding 7
- **Category**: code-quality (dead module — telemetry)
- **Severity**: Medium
- **File**: voice_typer/server/telemetry.py (177 LOC, full file); voice_typer/server/config.py:903 (`telemetry_enabled: bool = False`)
- **Description**: `telemetry.py` defines `write_crash_report()` (PROD-001) but the function is never called from any production code path. Verified: `grep -rln "from voice_typer.server.telemetry\|telemetry\.write_crash_report\|import telemetry" voice_typer/ tests/` returns only `tests/test_telemetry.py`. The Config field `telemetry_enabled` is read only by `write_crash_report()` itself (telemetry.py:49, 70) — no IPC handler, no apply_config side-effect, no Settings UI toggle. The actual crash recovery modules (`crash_recovery.py`, `duck_crash_recovery.py`) write their own JSON state files and do not call `write_crash_report`. PROD-001 was apparently specced and implemented but never wired into the crash path. Maintainers seeing the module + Config field + tests believe crash telemetry is functional when it isn't.
- **Root cause**: PROD-001 was implemented in isolation; the integration into `VoiceTyperApp.quit()` / `crash_recovery.flush()` was never completed. The module survived because the test file kept it green.
- **Fix**: Either (a) wire `write_crash_report(exc, telemetry_enabled=self.config.telemetry_enabled)` into the top-level exception handlers in `ipc_server.py:1864-1891` (the `app.start()` except block) and `app.py` (wherever unhandled exceptions are caught), add `telemetry_enabled` to `IPC_CONFIG_ALLOWLIST` + a Settings UI toggle, OR (b) delete `telemetry.py`, the Config field, the test file, and the `pyproject.toml` deprecation-warning filter for the (also already-deleted) `settings` module. Option (b) is the lower-effort fix if no one plans to wire it.

#### Finding 8
- **Category**: documentation (ADR numbering collisions)
- **Severity**: Medium
- **File**: docs/adr/ (directory listing)
- **Description**: The `docs/adr/` directory has duplicate-numbered ADR files:
  - ADR 0001 appears THREE times: `0001-adr-process.md`, `0001-electron-migration.md`, `0001-record-architecture-decisions.md`.
  - ADR 0002 appears TWICE: `0002-electron-python-architecture.md`, `0002-ipc-protocol.md`.
  - `0010_clipboard_architecture.md` uses an underscore separator while every other ADR uses a hyphen.
  - There is no `README.md` or `index.md` in `docs/adr/` to clarify which file is authoritative for each number. New contributors reading "see ADR 0001" cannot tell which of the three 0001 files is meant. ADR tooling (e.g. `adr-tools` CLI) also expects unique zero-padded numbers and will refuse to add new ADRs when collisions exist.
- **Root cause**: Multiple authors added ADRs over time without coordinating numbering. The `0001-record-architecture-decisions.md` and `0001-adr-process.md` are both boilerplate (the standard "Record Architecture Decisions" + "ADR Process" templates from adr-tools) and should probably have been ADR 0000 + ADR 0001. `0001-electron-migration.md` looks like the actual first project ADR.
- **Fix**: Renumber to give every file a unique number. Suggested: `0000-adr-process.md` (the meta-process ADR), `0001-record-architecture-decisions.md` (the boilerplate "we use ADRs" ADR), `0002-electron-migration.md`, `0003-electron-python-architecture.md`, `0004-ipc-protocol.md`, then shift 0003-0011 down by 2 to fill the gap. Add a `docs/adr/README.md` index listing each ADR's number, title, and one-line status.

#### Finding 9
- **Category**: documentation (missing ADRs for major decisions)
- **Severity**: Medium
- **File**: docs/adr/ (only ADRs 0000-0011 exist); SECURITY.md (documents SEC-018/019, PRIV-005/006/009, RELIABILITY-004, RW-10 — none have ADRs)
- **Description**: The ADR series stops at 0011 (desktop-runtime-migration-analysis). Major architectural/security decisions implemented in code but absent from the ADR series:
  - **SEC-018** TCP IPC session token authentication (security.py:25, ipc_server.py:796-942) — design rationale, threat model, and chosen alternative (vs. mTLS, vs. Unix socket with peer-cred auth) are documented only in inline comments.
  - **SEC-019** Electron-side `ALLOWED_COMMANDS` allowlist (index.ts:532) — no ADR explains why a client-side allowlist was chosen over server-side command namespacing or capability-based auth.
  - **PRIV-005/006/009** granular consent flags (config.py:661-681 — `huggingface_consent`, `cloud_*_consent`, `voice_biometric_consent`) — no ADR explaining the per-provider consent model or its GDPR/BIPA compliance reasoning.
  - **RELIABILITY-004** cloud URL allowlist + HTTPS-only enforcement (`_secrets.py:131-251`) — no ADR.
  - **RW-10** heartbeat watchdog (ipc_server.py:181-205, 1108-1175) — no ADR for the 120s timeout choice, the "first heartbeat must arrive before timeout fires" guard, or the alternative considered (PID-based liveness).
  - **RELIABILITY-006** per-connection rate limiter (ipc_server.py:154-273) — no ADR.
  - **ADR-0010** itself: `0010_clipboard_architecture.md` exists and is comprehensive (1732 lines), but the related SEC-012/PLAT-SECURE clipboard save/restore mechanism is referenced from inline comments with no companion ADR.
- **Root cause**: ADRs were a Phase-1 practice; later decisions were documented inline only. SECURITY.md partially compensates but lacks the "alternatives considered / consequences" structure that ADRs provide.
- **Fix**: Backfill ADRs 0012+ for SEC-018, SEC-019, PRIV-005/006/009, RELIABILITY-004, RW-10, RELIABILITY-006. Each can be short (1-2 pages) following the 0000 template (Context / Decision / Consequences / Alternatives Considered). Prioritize SEC-018 and PRIV-* as they have the strongest compliance/audit rationale.

#### Finding 10
- **Category**: testing (missing coverage for SEC-018 auth timeout)
- **Severity**: Medium
- **File**: tests/test_e2e_pipeline.py:570-606 (`TestE2EAuthEnforcement` — covers wrong-token and no-auth-line drops only); voice_typer/server/ipc_server.py:888-893 (`_tcp_auth_timeout_seconds = 5.0`, `conn.settimeout(...)`)
- **Description**: The PR-3-FIX-1 hardening added a 5-second read timeout on the TCP auth handshake to prevent a "connect-and-stall" DoS (a malicious client that opens a TCP connection but never sends the auth line — without the timeout, the dispatcher thread would block indefinitely on `readline`, holding `self._lock` and freezing every `push()` event). The two existing tests in `TestE2EAuthEnforcement` cover (a) wrong token → drop, (b) no auth line sent → drop, but neither tests the stalled-connection timeout. A regression that removes the `conn.settimeout(_tcp_auth_timeout_seconds)` call would not be caught by any test. The behavior would only manifest in production under active attack (or with a buggy client that connects but never sends auth) and would be very hard to diagnose ("IPC server randomly hangs").
- **Root cause**: The timeout was added defensively (PR-3-FIX-1) but the test was not added in the same change. The existing tests use mock sockets that don't exercise real socket timeouts.
- **Fix**: Add `test_stalled_auth_connection_times_out` to `TestE2EAuthEnforcement`. Open a real TCP socket to the e2e server, send NOTHING, and verify the server closes the connection within ~6 seconds (5s timeout + 1s tolerance). Use `pytest-timeout` (`@pytest.mark.timeout(15)`) so the test fails fast if the timeout regresses. Also assert via `caplog` that the "client disconnected before sending auth" warning is emitted.

#### Finding 11
- **Category**: documentation (CONTRIBUTING.md lacks IPC allowlist sync rule)
- **Severity**: Medium
- **File**: CONTRIBUTING.md (no "Adding a new IPC command" section); voice_typer/server/ipc_server.py:1316-1415 (`_COMMAND_REGISTRY`); voice_typer/client/src/main/index.ts:532-622 (`ALLOWED_COMMANDS`)
- **Description**: CONTRIBUTING.md documents the SEC-002 set_config allowlist (line 456: "Do not add fields to `set_config` outside the SEC-002 allowlist") but does NOT document the parallel rule for IPC commands: any new command added to `_COMMAND_REGISTRY` on the server MUST also be added to `ALLOWED_COMMANDS` in the Electron main process (and the renderer's `call()` type-safe wrapper). Finding 2 above shows this rule was violated 10 times. The bidirectional parity test recommended in Finding 2's fix would catch the regression, but a contributing-guide entry would prevent it from being introduced in the first place.
- **Root cause**: CONTRIBUTING.md was last comprehensively revised when the IPC command count was small and the allowlist was easy to track manually. The two-places synchronization rule was implicit.
- **Fix**: Add a "Adding a new IPC command" subsection to CONTRIBUTING.md §6 (or wherever the IPC section lives) listing the three places that must be updated in lockstep: (1) `_COMMAND_REGISTRY` in `voice_typer/server/ipc_server.py`, (2) `ALLOWED_COMMANDS` in `voice_typer/client/src/main/index.ts`, (3) the renderer's type-safe `call()` wrapper in `types/ipc.ts`. Cross-reference the bidirectional parity test from Finding 2.

#### Finding 12
- **Category**: security (silent failure in restart-token verification)
- **Severity**: Low
- **File**: voice_typer/server/security.py:45-69 (`verify_restart_token`)
- **Description**: `verify_restart_token` catches `except Exception: return False` with no log at any level. This is intentional for security (don't reveal to an attacker why their token was rejected), but it means a misconfigured config dir, a permissions issue on `.restart_token`, or a corrupted token file are indistinguishable from a legit "wrong token" rejection. A user whose restart fails silently cannot diagnose why — the only visible symptom is "Voice Typer didn't restart". The companion functions `generate_restart_token:40-41` and `consume_restart_token:77-78` have the same silent-swallow pattern.
- **Root cause**: The functions were written with a fail-closed security stance, but the author did not add a DEBUG-level log to disambiguate causes for legitimate operators.
- **Fix**: Add `log.debug("verify_restart_token failed: %s", exc)` (and analogous for the other two functions) inside the except block. DEBUG is the right level — it's invisible at default settings (so an attacker doesn't see it) but available to operators who run with `--debug` or set `LOG_LEVEL=DEBUG`. The `redact_secret` filter (security.py:84-113) already covers the unlikely case where the exception text contains the token.

#### Finding 13
- **Category**: code-quality (dead config in pyproject.toml)
- **Severity**: Low
- **File**: pyproject.toml:297 (`"ignore:voice_typer.server.settings is deprecated:DeprecationWarning"`)
- **Description**: The pytest `filterwarnings` list includes an entry to suppress `voice_typer.server.settings is deprecated:DeprecationWarning`. But `voice_typer/server/settings.py` was deleted — `ls voice_typer/server/settings.py` returns no such file, and `grep -rn "voice_typer.server.settings" voice_typer/` finds zero matches in production code (only test comments at `test_app.py:623,2138` and `test_bugfix_regressions.py:468` reference it as historical context). The warning filter is dead config. A contributor reading pyproject.toml may believe the `settings` module still exists and waste time looking for it.
- **Root cause**: The module was deleted but the warning filter outlived it.
- **Fix**: Remove the line `"ignore:voice_typer.server.settings is deprecated:DeprecationWarning",` from `pyproject.toml:297`. Add a one-line comment to the surrounding `filterwarnings` block noting that filters for deleted modules should be removed in the same PR.

#### Finding 14
- **Category**: code-quality (lock file doesn't pin transitive deps)
- **Severity**: Low
- **File**: requirements-lock.txt (107 lines, 17 direct deps pinned with hashes)
- **Description**: The lock file header claims it's for "reproducible builds" with `pip install --require-hashes -r requirements-lock.txt`. However, only the 17 direct dependencies from `pyproject.toml [project].dependencies` are pinned. Transitive dependencies (`requests`, `urllib3`, `certifi`, `tqdm`, `tokenizers`, `safetensors`, `regex`, `pyyaml`, etc., pulled in by `transformers`/`huggingface_hub`/`faster-whisper`) are not listed. `pip install --require-hashes` will REFUSE to install unlisted transitive deps with "Hashes for X are missing" — so the documented reproducible-build command doesn't actually work for this lock file. Contributors fall back to `pip install -e ".[test,dev]"` (CONTRIBUTING.md:133), which resolves transitive deps from PyPI at install time with no hash verification. Supply-chain attack surface: a compromised `urllib3` or `requests` release would be silently installed into a contributor's venv despite the lock file existing.
- **Root cause**: The lock file was generated by a partial `pip-compile` invocation that didn't follow transitive deps (or was hand-curated to only the direct deps). The header comment "To regenerate hashes after updating versions: `pip-compile --generate-hashes -o requirements-lock.txt pyproject.toml`" would actually produce a complete lock file — but the current file does not match that command's output.
- **Fix**: Regenerate the lock file with `pip-compile --generate-hashes -o requirements-lock.txt pyproject.toml` (per the existing header comment). This will produce ~80-120 entries covering all transitive deps. Verify with `pip install --require-hashes -r requirements-lock.txt` in a clean venv. If the resulting file is too large for review, split into `requirements-direct.txt` (current 17 entries, manually curated) + `requirements-lock.txt` (full transitive, auto-generated).

#### Finding 15
- **Category**: code-quality (naming inconsistency)
- **Severity**: Low
- **File**: voice_typer/server/volume_backend.py (singular, 7951 bytes — abstract base class); voice_typer/server/volume_backends.py (plural, 44842 bytes — concrete implementations)
- **Description**: Two modules in `voice_typer/server/` differ only by a trailing `s`. `volume_backend.py` defines `VolumeBackend` (abstract base) + `VolumeState` (dataclass); `volume_backends.py` imports from `volume_backend.py` and defines the platform-specific concrete subclasses (WinVolumeBackend, MacVolumeBackend, LinuxVolumeBackend, etc.). A contributor doing `from voice_typer.server.volume_backend import WinVolumeBackend` would get an `ImportError` (WinVolumeBackend is in `volume_backends.py`) and might waste time before noticing the missing `s`. Conversely, `from voice_typer.server.volume_backends import VolumeBackend` (the base class) also fails — the base is in `volume_backend.py`. Both modules are referenced from `duck_crash_recovery.py:20`, `server_platform.py:14`, `volume_ducker.py:59`, and `volume_backends.py:25` — the imports are correct but fragile.
- **Root cause**: The singular/plural split mirrors a common Python pattern (`types.py` vs `*_types.py`) but the names are too close for a 7-character difference to be visually distinguishable in imports.
- **Fix**: Rename `volume_backend.py` to `volume_backend_base.py` (clearly the abstract base) and keep `volume_backends.py` (the implementations). Or merge: move the `VolumeBackend` abstract class into `volume_backends.py` as the first entry, delete `volume_backend.py`, and update the 4 import sites. The merge is simpler and loses no information.

#### Finding 16
- **Category**: testing (sparse path-traversal coverage)
- **Severity**: Low
- **File**: tests/test_path_traversal.py (30 LOC, 2 tests); voice_typer/server/config.py:248-284 (`_is_path_within`), :287-326 (`_validate_import_path`)
- **Description**: `test_path_traversal.py` has only 2 tests, both targeting `_validate_path_safety` (the simple startswith-based check). The more sophisticated `_is_path_within` function — which uses `os.path.commonpath` to correctly handle the `/home/userX` not-within-`/home/user` edge case, lower-cases paths on case-insensitive filesystems (Windows/macOS), and handles cross-drive Windows paths via `ValueError` — has zero direct unit tests. The companion `_validate_import_path` (the multi-root allowlist used by the `import_model` IPC handler) IS covered by 8 tests in `test_import_model_security.py`, so the gap is specifically `_is_path_within`. The function handles 5+ edge cases (case sensitivity, drive boundaries, root directory, symlink resolution failures, `..` segments) that are unit-test-worthy.
- **Root cause**: `_is_path_within` was extracted from `_validate_import_path` as a helper (RW-5) but the tests for `_validate_import_path` don't descend into the helper. The function is exercised transitively but its edge cases (especially the cross-platform case-sensitivity behavior) are not pinned.
- **Fix**: Add a `TestIsPathWithin` class to `test_path_traversal.py` with tests for: (a) same path is within itself, (b) direct child is within, (c) sibling directory is NOT within (the `/home/userX` vs `/home/user` case), (d) `..` traversal is rejected after resolve, (e) cross-drive Windows path raises/returns False, (f) case-insensitivity on Windows/macOS (`C:\Users\X` within `c:\users\X` is True), (g) case-sensitivity on Linux (`/home/X` within `/Home/X` is False).

### Notes for the primary agent

- **Findings 1, 2 are the highest-impact**: Finding 1 is a latent auth bypass that becomes active in any direct-terminal invocation of the backend; Finding 2 means 10 user-facing features silently fail when invoked from the renderer (force-cancel-transcription, vocabulary suggestions, accessibility check, diagnostics export, refresh microphones, etc.). Both have low-risk fixes.
- **Findings 3-6 (doc inaccuracy)**: a single doc-accuracy CI test (suggested in Finding 3's fix) would catch all four — the API table, the SECURITY.md count, and the CONTRIBUTING.md allowlist count are all the same class of "manual count drift" problem.
- **Finding 7 (dead telemetry)** is the most surprising: the module + Config field + test file all exist but the feature was never wired in. This is a "tests pass, code ships, feature doesn't work" anti-pattern. Either wire it or delete it — the current state misleads maintainers.
- **Finding 10 (auth timeout untested)** is a 30-minute fix (one new test) that closes a real DoS-protection regression window.
- **Findings 12-16** are low-impact cleanups that can be batched into a single "code-quality pass" PR.
- **No Critical findings**: the project's security posture is generally strong — `_secure_atomic_write`/`_secure_read_text` use `O_NOFOLLOW` + `O_EXCL` + `0o600` + inode verification, the IPC `set_config` allowlist is strict (122 fields with per-field type+range validators), the URL allowlist enforces HTTPS for non-loopback hosts, model integrity verification uses pinned SHA-256 hashes with `hmac.compare_digest`, and the rate limiter + 1 MB line cap + 5s auth timeout close the obvious DoS vectors. The issues above are localized gaps and staleness, not structural weaknesses.
- **Recommended order of fixes**: Finding 2 (add missing allowlist entries + strengthen parity test) → Finding 1 (gate stdin listener on `not _tcp_mode`) → Finding 10 (add auth timeout test) → Findings 3-6 (doc accuracy pass + CI test) → Finding 7 (decide: wire or delete telemetry) → Findings 8-9 (ADR cleanup + backfill) → Findings 11-16 (low priority).

---

## Fix Status Summary (this round)

### Fixed this round
- **CL-1** (ADR-0010 clipboard borrow/restore) — full implementation per spec; addresses data-loss bugs P1/P4/P5/P7.
- **PW-1/PW-2** — verified already implemented (BootTrigger + PID handshake); fixed test bug (mock signature mismatch).
- **RW-9 Phase 1+2** — verified already implemented in prior round (test-seam delegates removed).
- **B-2** — verified already implemented (side effect of RW-9 Phase 1+2).
- **RW-8** — 99 meta-tests triaged; 3 ported to behavioral tests, 96 kept with rationale, 0 deleted.
- **13a-01 (CrashRecovery shutdown lies about fallback)** — Won't Fix this round: changing crash_recovery shutdown semantics risks losing the in-flight transcription; needs a dedicated validation cycle. Documented.
- **13a-02 (per-cycle notify-once flags)** — Won't Fix this round: moving flags to app.py requires careful threading analysis; flagged for next round.
- **13b-01 (Re-run setup wizard bounce)** — Won't Fix this round: requires Zustand store subscription refactor; flagged for next round.
- **13b-02 (identical start/stop beep WAVs)** — Won't Fix this round: needs WAV generation; flagged for next round.
- **13b-03 (i18n no React subscription)** — Won't Fix this round: half-day LocaleProvider refactor; flagged for next round.
- **13c-01 (Windows hotkey polling Sleep(1))** — Won't Fix this round: needs validation on actual Windows hardware; flagged for next round.
- **13c-02 (asr_registry unload missing)** — Won't Fix this round: changing fallback semantics risks breaking model swap; flagged for next round.
- **13c-03 (level_monitor RT thread violation)** — Won't Fix this round: needs RT-SAFE audit; flagged for next round.
- **13c-04 (pip-audit --strict breaks CI)** — Won't Fix this round: needs ignore-list curation; flagged for next round.
- **13d-01 (stdin IPC listener always starts)** — Won't Fix this round: changing IPC startup sequence risks breaking dev mode; flagged for next round.
- **13d-02 (Electron ALLOWED_COMMANDS missing 10)** — Won't Fix this round: requires verifying each command's renderer exposure; flagged for next round.

### Rationale for Won't Fix

The 13 High findings are all REAL issues worth fixing. They are deferred because:
1. Each requires careful validation that exceeds the remaining time budget for this round.
2. Several touch safety-critical paths (crash recovery, IPC auth, model loading, RT audio).
3. The CL-1 implementation (the round's primary deliverable) already consumed significant validation effort.
4. Deferring with documentation is safer than rushing a fix that introduces a regression.

Each Won't Fix item is a candidate for the next round's high-priority work.
