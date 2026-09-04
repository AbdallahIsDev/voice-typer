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

### GQ-32 — text_cleanup max-size corrections file drives 145 ms per-dictation
**Status:** 🚫 Won't Fix (lowering SEC-011 cap from 5000→500 is a user-facing behavior change for power users; deferred to dedicated perf-tuning session)
> - **2026-08-30 verification:** rationale stands — file now a package; SEC-011 cap 5000 verified at `text_cleanup/_corrections_data.py:324-325` (pinned by tests/test_security_hardening.py:419); the combined regex is identity-cached (`_engine.py:69-78`, `:126-140`), so the cost is per-call `re.sub` matching, not regex construction. Both mitigations (cap 500 → product change; Aho-Corasick → new dependency) remain deliberate deferrals.
**Description:** With bundled corrections.json (8 phrases), `clean_transcribed_text` on a 5580-char input measures median 7.9ms / p95 8.4ms — well under Low threshold. But with a SEC-011-maximum (5000 phrases + 5000 extra-word patterns) user corrections file, the combined-alternation regex `(?:p1|p2|...|p5000)` built at line 607 drives per-dictation cleanup to median 145.4ms / max 199.7ms on a 2360-char input, and p95 211.2ms on a 47-char input with one match (first-call regex warmup).
**User Impact:** For typical users — none (<10ms). For users with very large corrections dictionaries — per-dictation cleanup could approach 200ms, which on a 1-second transcription budget is ~20% overhead.
**Root Cause:** The SRE trie compiled from a 5000-alternative alternation of `re.escape`d literals is O(total pattern chars), and `re.sub` against it touches every text char against the trie.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/text_cleanup/_engine.py` (formerly `text_cleanup.py:566-608` — package split)
- `voice_typer/server/text_cleanup/_corrections_data.py:324-327`
**Fix:** If max-size corrections files become a real use case, options are: (a) lower the SEC-011 cap from 5000 to ~500 (still 60x the bundled defaults); (b) switch from a single combined regex to Aho-Corasick (`pyahocorasick` package) for O(N+M) multi-pattern matching that scales better than SRE trie at 5000+ patterns. Recommend (a) as the lowest-risk mitigation.
**Severity:** 🟡 Medium


### GP-119 — multi-key chord support
**Status:** 🚫 Won't Fix (disposition accurate — re-audited 2026-08-12: no sequence-chord support found; only single-combo multi-key hotkeys, e.g. Ctrl+Shift+V, exist)
**Severity:** 🟢 Low


### GQ-L27 (+ER-35) — event dual-channel emit (specific channel + generic python-event envelope)
**Status:** 🚫 Won't Fix BY DESIGN (2026-08-24 audit) — the dual-channel emit IS the documented ADR-0020 §9 contract: the bubble window listens on the specific channel, usePython on the generic one; ≤30 Hz coalescing makes the clone cost immaterial.
**Severity:** 🟢 Low


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

---

## BP Session additions (2026-09-03 — investigation-only session, Groups 1 & 2)

> Eight new findings from the BP investigation concluded to be not worth solving (per C-REVIEW-2, documented here rather than review.md). These are NEW conclusions by the investigating/reviewing agents; the user may reverse any of them — see the recommendation line in each entry.

### BP-WF-2 — restart-backend returns reason "relaunching" on spawn failure
**Status:** 🚫 Won't Fix (Electron-only path; cosmetic; no consumer switches on the value — verified)
**Description:** `voice_typer/client/src/main/python/restart-backend.ts:128-135` returns `{ok: false, reason: "relaunching"}` when a spawn fails (no relaunch in flight) — misleading reason enum reuse on a path slated for removal.
**Fix (if reversed):** Distinct reason string "spawn_failed". Found by: W1-A1.
**Severity:** 🟢 Low

### BP-WF-3 — IPC command registry maps command names to method-name strings
**Status:** 🚫 Won't Fix (works, init-time-validated, parity-pinned; a decorator registry would churn ~10 test files for zero user impact)
**Description:** `_COMMAND_REGISTRY` (74 entries) maps commands to method-name strings resolved via getattr; construction-time validation closes the failure mode; the parity test depends on command names, not the indirection.
**Fix (if reversed):** `@command("...")` decorator registration. Found by: W1-A2; recommended Won't Fix by A2 + W4-R4.
**Severity:** 🟢 Low

### BP-WF-5 — Three parallel toast-cooldown stores (50/65/73 lines)
**Status:** 🚫 Won't Fix (small, working, domain-scoped; consolidation is churn — a factory saves ~60 lines while each store keeps its module-level HMR isolation by design)
**Description:** deviceLostStore (50L), degradationToastStore (65L), lastResortToastStore (73L) share the same shape (timestamps + setters + resetForTest) with the same documented HMR rationale. Found by: W1-A3; line count corrected by W2-R2/W4-R4.
**Fix (if reversed):** `createCooldownStore()` factory. Found by: W1-A3.
**Severity:** 🟢 Low

### BP-WF-6 — Per-chunk module import inside the level-monitor lock
**Status:** 🚫 Won't Fix (µs-scale, below the W1 value bar; hoisting would add a circular-import workaround for zero measurable gain)
**Description:** `voice_typer/server/level_monitor/worker.py:611,391,594` execute `from .monitoring import ...` inside the lock-held write block, per chunk (~16-31Hz). Found by: W1-A5.
**Severity:** 🟢 Low

### BP-WF-8 — Sentence-casing pass is a per-character Python loop
**Status:** 🚫 Won't Fix (µs-scale at typical dictation lengths; sub-threshold)
**Description:** `voice_typer/server/text_cleanup/_casing.py:11-21` does list(text) + per-char loop + join where a precompiled regex would run in C. Semantics preservation is the only risk of changing it. Found by: W3-A6.
**Severity:** 🟢 Low

### BP-WF-9 — theme_icon re-decodes the embedded PNG on every theme flip
**Status:** 🚫 Won't Fix (theme flips are rare manual OS toggles — a cache would be over-engineering for a twice-a-year event; no cache exists unlike TRAY_ICON_CACHE)
**Description:** `src-tauri/src/theme_icon.rs:50-67` — `image_for_theme` re-decodes the embedded PNG (`Image::from_bytes`) on every `apply_to_window`/`apply_startup` call. Frequency is ~once per session; cost is one µs-ms decode.
**Fix (if reversed):** mirror the TRAY_ICON_CACHE pattern for window icons. Found by: W1-A1 (2026-09-04 BP session, Wave 1).
**Severity:** 🟢 Low

### BP-WF-10 — frame-reader subarray keeps parent ArrayBuffer alive
**Status:** 🚫 Won't Fix (bounded by the 1 MiB SEC-023 frame cap; adjacent to WONT_FIX'd GQ-L36; zero practical retention)
**Description:** `voice_typer/client/src/main/python/tcp/frame-reader.ts` slices incoming buffers with subarray, retaining the parent backing store. Retention is capped at the 1 MiB frame limit.
**Fix (if reversed):** copy on slice in the overflow path. Found by: W3-A6 (2026-09-04 BP session, Wave 3).
**Severity:** 🟢 Low

### BP-WF-11 — retry-scheduler retries every 2 s forever while the backend process is alive
**Status:** 🚫 Won't Fix (deliberate design — the renderer-side restart escalation is the user exit; bounded by process lifetime)
**Description:** `voice_typer/client/src/main/python/tcp/retry-scheduler.ts` retries the TCP connection indefinitely at 2 s intervals while pythonProcess is alive. Documented; the renderer "restart backend" escalation is the exit path.
**Fix (if reversed):** exponential backoff with a cap. Found by: W3-A6 (2026-09-04 BP session, Wave 3).
**Severity:** 🟢 Low

### BP-WF-12 — dropdown-menu per-slot class-string duplication
**Status:** 🚫 Won't Fix (matches upstream shadcn convention — per-slot class strings are the library's idiom; consolidating forks from upstream)
**Description:** `components/ui/dropdown-menu.tsx` Item/CheckboxItem/RadioItem/SubTrigger share ~90% of one class stack. This is the shadcn primitive convention.
**Fix (if reversed):** extract a shared base class const. Found by: W3-A8 (2026-09-04 BP session, Wave 3).
**Severity:** 🟢 Low

### BP-WF-13 — volume restore() holds its lock through the fade (~150-200 ms ESC serialization)
**Status:** 🚫 Won't Fix (acknowledged in-code trade-off; ESC path serialization is bounded and rare)
**Description:** `voice_typer/server/volume_ducker.py` — restore() runs the fade subprocess while holding self._lock, serializing concurrent volume operations for the fade duration. Partially documented in-code.
**Fix (if reversed):** release before fade, re-acquire to publish state. Found by: W3-A5 (2026-09-04 BP session, Wave 3).
**Severity:** 🟢 Low
