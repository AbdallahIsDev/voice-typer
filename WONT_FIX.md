# Won't Fix — Deliberately Not Solved

> **HARD RULE (C-REVIEW-1):** Entries in this file are DELIBERATE
> decisions to leave the code exactly as-is. Do NOT fix, re-implement,
> modify, or "improve" any of them, and do NOT change their status. They
> are excluded from `review.md` so agents processing the active task
> queue never read them (token efficiency). An agent that "solves" a
> Won't Fix entry — or re-diagnoses it as fixable and fixes it —
> violates AGENTS.md Hard Don'ts `C-REVIEW-1`.
>
> If you believe a Won't Fix decision should be reversed, say so in the
> chat report / worklog for the USER to decide. Never change the status
> yourself.

---

### GQ-48 — history_db LIKE fallback 58 ms scan on separator-only queries
**Status:** 🚫 Won't Fix (LIKE fallback 58ms scan is edge case — separator-only queries; idx_timestamp_id already mitigates ORDER BY)
**Description:** EXPLAIN QUERY PLAN: `SCAN transcriptions USING INDEX idx_timestamp` + `USE TEMP B-TREE FOR LAST TERM OF ORDER BY`. The `WHERE text LIKE ? ESCAPE '\\'` with leading `%` cannot use any index, forcing a full table scan. Benchmark on 500K-row DB: `search(query="%", limit=50)` = 58ms median. Scales linearly with N (was 5.7ms at 50K rows — 10× rows ≈ 10× time). Triggered when `_is_fts_compatible_query` returns False (query contains ONLY separator chars — `%`, `_`, punctuation).
**User Impact:** Edge-case scenario (user types only `%` or `_` in search box). At 5M rows would hit ~580ms (Critical). Bounded by `_MAX_LIST_LIMIT=500` on the result set, but the SCAN cost is unbounded.
**Root Cause:** LIKE with leading `%` cannot use any index.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/history_db_internals/search.py:382, 412, 524` (LIKE fallback MOVED out of history_db.py — re-audited 2026-08-12: the cited history_db.py:2430-2484 range now contains `wal_checkpoint` code; `prepare_like_search_pattern` / `is_fts_compatible_query` live in search.py)
**Fix:** For separator-only queries, prefer an FTS5 substring search via `MATCH '"*<char>*"'` tokenization (limited support in unicode61). Alternatively, reject these queries client-side. Low priority — edge case.
**Severity:** 🟡 Medium


### GQ-L8 — audio_filters/base.py per-chunk list(self._filters) snapshot allocation
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/audio_filters/base.py:162-163`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low


### GQ-L10 — audio_quality.py analyze_chunk retained in production for tests
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
> - **2026-08-24 audit:** zero production callers confirmed (docstring admits retained-for-tests) — move to test helpers per E15.
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/audio_quality.py:160-197`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low


### GQ-L11 — audio_filters/base.py swap race causes single-chunk audio glitch
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/audio_filters/base.py:288-344`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low


### GQ-L18 — config/loader.py re-reads config.json after migrate_secrets_to_keyring
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/config/loader.py:246-271`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low


### GQ-L24 — parakeet_engine _warm_up_model uses 0.5s silence (production chunks are 25s)
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/parakeet_engine.py:1462-1475`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low


### GQ-L33 — sidecar_cmds.rs SeqCst where weaker orderings suffice (next_id, shutting_down)
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- sidecar_cmds.rs was SPLIT (EO-35) into submodules and is now only 55 lines; SeqCst usages moved to `commands/dispatch.rs:214, 227` and `shutdown.rs:48`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low


### GQ-L34 — single_instance.ts sync mkdirSync + writeFileSync on boot path
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/main/single_instance.ts:69-85`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low


### GQ-L36 — tcp-connect.ts Buffer.concat per chunk
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/main/python/tcp-connect.ts:239-241`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low


### GQ-L37 — show-hide.ts setImmediate retry on every show (defensive)
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/main/windows/bubble/show-hide.ts:167-184`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low


### GQ-L38 — window-handlers.ts dynamic import('../i18n') on every locale change
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/main/ipc/window-handlers.ts:349-357`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low


### GQ-L40 — color-utils _cssColorToHexViaDOM no per-input cache
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
> - **2026-08-24 audit:** DOM path hit on theme derivation/hover (useThemeSettings:171/199, theme-palette:79, theme-contrast:102-116); small Map cache suffices.
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/lib/color-utils.ts:218-248`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low


### GQ-L42 — sound-manager 4 capture-phase window listeners (pointerdown redundant)
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/lib/sound-manager.ts:315-318`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low


### GQ-L43 — format.ts unbounded _numberFormatCache Map (bounded in practice ≤48)
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/lib/format.ts:90`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low


### GQ-L44 — useThemeSettings.ts useEffect with no dep array (runs every commit)
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/components/settings/useThemeSettings.ts:431-433`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low


### GQ-L46 — Sidebar.tsx inline closures per nav item (10 allocs per render)
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/components/layout/Sidebar.tsx:300`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low


### GQ-L47 — ThemeSettingsSection.tsx 648 LOC mixing 4 sub-sections
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
> - **2026-08-24 audit:** file now JSX-only (state machine/colors/contrast/draft extracted); residual = custom-picker block :429-618.
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/components/settings/ThemeSettingsSection.tsx:1-648`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low


### GQ-L53 — generate_beeps.py per-sample struct.pack loop
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `scripts/build/generate_beeps.py:73-101`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low


### GQ-L54 — check_branding.py 314ms wall (could use ripgrep)
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `scripts/check_branding.py:251-275`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low


### GQ-L56 — credential_store _run_keyring_call orphan thread count not hard-capped
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/credential_store.py:224-270`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low


### GQ-L58 — model_manager _evict_lru_model: refactor along with GQ-6/GQ-7/GQ-29 (single coordinated fix)
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/model_manager.py:1748-1758`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low


