# Verification & Scoring Audit of `review.md`
**Date:** 2026-08-18 · **Mode:** 7 parallel inspector sub-agents (A–G) + orchestrator ground-truth re-measurement
**Scope:** all ~150 findings in `review.md` (2,763 lines, 194 headings) verified against the live codebase
**Method:** every claim re-checked with `rg`/file reads/LOC measurement; stale entries re-baselined

> Ground truth note: Inspector-A and Inspector-D produced conflicting LOC numbers for several
> monoliths. Orchestrator re-measured directly immediately after: app.py=1676, transcription.py=1333,
> sidecar_ws.py=1827, recorder.py=2584, cloud_engines.py=948, config/__init__.py=2389,
> credential_store.py=1931, history_db.py=2255, crash_recovery.py=1176, shutdown_controller.py=1287,
> ws.rs=959, state.rs=898, logging.rs=1677. Inspector-A's numbers match; Inspector-D's were stale
> for those files. Final report uses the direct measurement.

---

## Executive Summary Table

### Tier 1 — Critical / safe quick wins (score ≥80)

| Task Name | Category | Verdict | Score | Risk | Sub-Agent |
| :--- | :--- | :--- | :---: | :--- | :--- |
| GP-44 — RPM depends on wrong webkit2gtk3 | Packaging | **KEEP** | 95 | Low — 1-line conf edit | G |
| GP-15 — wtype missing from deb/rpm depends | Packaging | **KEEP** | 88 | Low — 2-line conf edit | G |
| TC-1 — pytest `--dist=loadgroup` wiring | Testing | **KEEP** | 88 | Low | E |
| EO-14 — `HandlerBase._wrap` defined but unused (26 copy-paste sites) | Maintainability | **KEEP** | 82 | Low — mechanical | D |
| TC-27 — `time.time()` deadlines in 36 test sites | Testing | **KEEP** | 80 | Low — mechanical swap | E |
| VP-40 — `CrashRecovery.__del__` 102 lines | Maintainability | **KEEP** | 80 | Low | D |

### Tier 2 — High value, moderate effort (60–79)

| Task Name | Category | Verdict | Score | Risk | Sub-Agent |
| :--- | :--- | :--- | :---: | :--- | :--- |
| EO-13 / AC-73 — orchestrator.run() 453-LOC method | Maintainability | **KEEP** | 70 | Med — hottest dictation path | A/D |
| FI-S10 — config_validators/__init__.py 862 → allowlist+entry_points | Maintainability | **KEEP** | 75 | Low (keep SEC-002 compat) | D |
| VP-38 — startup_sequence.run 925-line god-method | Maintainability | **KEEP** | 75 | Med | D |
| TC-43 — @types/node ^26 vs Node 24 runtime | DX | **KEEP** | 72 | Low — downgrade types only | E |
| VP-36 — config_path_safety.py half-done shim | Maintainability | **KEEP** | 72 | Low | D |
| EO-15 / VP-37 — clipboard paste() 542-LOC god-method | Maintainability | **MODIFY** | 72 | Med — safety-critical | D |
| EO-5 / FI-S7 — cloud_engines.py 948-LOC split | Maintainability | **KEEP** | 60 | Low (leaf-ish) | D |
| GQ-42 — microphone watcher idle-gate wired but zero callers | Correctness | **MODIFY** | 65 | Med — behavior inverted | E |
| SI-17 — duplicated PROTOCOL_VERSION, divergent enforcement | Correctness | **MODIFY** | 62 | Med — parity tests | C |
| EO-1 — VoiceTyperApp.__init__ ~630-line god-constructor | Maintainability | **KEEP** | 65 | Med | D |
| EO-12 / AC-131 / FI-S3 — config/__init__.py 2389-LOC stalled split | Maintainability | **KEEP** | 60 | Med | A/D |
| FI-S2 / AC-128 / GQ-70 — credential_store.py 1931-LOC split | Maintainability | **KEEP** | 60 | Med — secrets logic | A/D |
| FI-S1 — history_db.py 2255-LOC partial-split completion | Maintainability | **KEEP** | 60 | Med | D |
| EO-3 / FI-S4 / GQ-24 — sidecar_ws.py 1827-LOC monolith | Maintainability | **KEEP** | 65 | Med-High — core IPC | D |
| FR-44 — Rust RotatingFileWriter holds Mutex across blocking I/O | Performance | **KEEP** | 60 | Med-High | G |
| FR-52 — bare dict/list on ConfigApplier/ServiceProtocol | Type safety | **KEEP** | 55 | Low-Med | G |
| EO-19 — crash_recovery/autostart_windows/startup_sequence/autostart_launcher >800 | Maintainability | **MODIFY** | 60 | Med (C-CROSS pins) | D |
| IN-3 — lazy-property retry: failure sentinel missing | Performance | **KEEP** | 60 | Med | C |
| GQ-48 — history_db LIKE fallback on separator queries (Won't-Fix stands) | Performance | **KEEP** | 60 | Low | E |
| GQ-66 — parallelize local Nuitka builds on Win/macOS | DX | **KEEP** | 60 | Low (local scripts only) | E |
| GQ-69 — _LEAKED_WORKERS unbounded (Won't-Fix stands) | Correctness | **KEEP** | 60 | Nil in production | E |
| UU-35 — macOS mic watcher lifetime-scoped | Performance | **KEEP** | 58 | Low-Med | C |
| AP-10 — log.exception source-line PII (147 sites) | Privacy | **KEEP** | 55 | Low | C |
| AP-47 — log.error→log.exception (213 sites) | Observability | **KEEP** | 55 | Low | C |
| WM-9 — _WRITE_FUTURE_TOTAL_TIMEOUT dead; writer loop lacks cap | Correctness | **KEEP** | 55 | Med — hang risk | G |
| SI-25 / VP-30 — state.rs 898-LOC mixed purpose split | Maintainability | **KEEP** | 55 | Low | C |
| GQ-11 — platform/logging.rs 1677 LOC 7-file split | Maintainability | **KEEP** | 55 | Med (C-LOG-1 pins) | D |
| AC-66 — VoiceTyperApp private state in 7 modules (~21 sites) | Architecture | **MODIFY** | 52 | Med — busy-flag semantics | A |
| GG-67-70 — Home/Onboarding/History page splits incomplete | Maintainability | **KEEP** | 52 | Low | C |
| XZ-R11-04 — no encryption at rest for dictated text | Security | **KEEP** | 50 | Med — privacy | A |
| S1-CR-67 / ZR-38 / DT-38 — sys.modules test-patch shims (907 LOC) | Maintainability | **MODIFY** | 50 | Med | A |

### Tier 3 — Moderate / valid secondary (30–59)

| Task Name | Category | Verdict | Score | Risk | Sub-Agent |
| :--- | :--- | :--- | :---: | :--- | :--- |
| AC-134 / EO-4 / GQ-25 — transcription.py 1333-LOC god-class | Maintainability | **KEEP** | 50 | Med | A/D |
| SI-27 — hotkey-utils.ts 859-LOC 5-concern monolith | Maintainability | **KEEP** | 50 | Low | C |
| EO-8 / GQ-38 / WM-5 — recorder.py 2584 LOC split | Maintainability | **MODIFY** | 50 | Med | D |
| FI-S6 / GQ-68 / VP-39 — shutdown_controller retarget to _do_cleanup | Maintainability | **MODIFY** | 45 | Med | D/E |
| ARCH-12 / S3-CR-21 / FZ-8 — inspect.getsource tests (467/149) | Testing | **MODIFY** | 45 | Low-Med | A |
| XA-5 — feature-page UX friction (22 live sub-items) | UX | **MODIFY** | 45 | Low | A |
| ER-2 — DeepFilterNet enhance() missing (RNNoise fallback works) | Correctness | **MODIFY** | 45 | Med | B |
| ER-48 — stuck transcription thread not fenced | Correctness | **KEEP** | 45 | Med | B |
| DJ-14 — GPU→CPU fallback cold-loads CPU model | Performance | **KEEP** | 45 | Med | B |
| EO-17 / CSTYLE-1 — task-ID comment sweep (4000+ occurrences) | Style | **MODIFY** | 45 | Med — grep-pinning tests | C/D |
| ARCH-9 — app.py test-seam re-exports (205 monkeypatch sites) | Testing | **MODIFY** | 42 | Low-Med | A |
| AC-136 / GQ-28 — model_manager.py 2494 LOC (+parakeet 1011, model.py 1238) | Maintainability | **MODIFY** | 45 | Med | A |
| FR-4/10/14/51/54 — claimed test files absent (status correction) | Testing | **KEEP** | 40 | Low | G |
| AP-45 — load_with_fallback has no timeout | Correctness | **KEEP** | 42 | Med | C |
| AP-46 — cloud 200-with-empty-body → "" | Correctness | **KEEP** | 40 | Low | C |
| XS-42 / SI-29 — _make_fake_* duplication (20 files) | Testing | **MODIFY** | 40 | Low | A/C |
| ER-18 — audio buffer 2×N sustained duplication | Performance | **MODIFY** | 40 | Med | B |
| FR-6 / GP-6 — Windows long-path prefix missing | Correctness | **KEEP** | 40 | Med (C-CROSS) | G |
| FZ-27 / YJ-15 — thiserror declared, zero usage; VoiceTyperError never started | Maintainability | **KEEP** | 40 | Med | B |
| ER-35 — bubble_level double-emit (intentional, test-pinned) | Performance | **KEEP** | 40 | Low (revisit if perf-changed) | B |
| AP-3 — export size cap missing | Correctness | **KEEP** | 38 | Med | C |
| TEST-2 — 410 time.sleep calls / 151 files | Testing | **MODIFY** | 38 | Low | A |
| FI-S list — Phase 4.5 splits umbrella (stale counts) | Maintainability | **MODIFY** | 38 | — | A |
| XA-2 — inconsistent loading/empty/error patterns | UX | **KEEP** | 35 | Low | A |
| QV-7 — error EmptyState: Dashboard done, Settings/Models open | UX | **MODIFY** | 35 | Low | G |
| ER-93 / FZ-60 — kill_process_tree unconditional 200ms sleep | Performance | **KEEP** | 25 | Low | B |
| GP-65 — build_tauri_all.sh --sign exits 0 | DX | **KEEP** | 35 | Low | G |
| ZR-84 — autostart_launcher.py 1232 LOC split | Maintainability | **KEEP** | 35 | Med (C-CROSS-3/5 pins) | B |
| GQ-3 — first-save penalty already absorbed | Performance | **MODIFY** | 35 | Nil | E |
| GQ-15 — bench README stale claim | Docs | **KEEP** | 45 | Nil | E |
| GQ-33 — noise_gate residual state-machine loop (doc fix only) | Performance | **MODIFY** | 30 | Nil | E |
| GQ-41 — recorder start() critical path (host-blocked) | Performance | **KEEP** | 35 | Host-only | E |
| WR-4 — audio_test.py 1366-LOC monolith remains | Testing | **MODIFY** | 30 | Low | B |
| XV-105 — hotkey pooling mostly done; multi-spec collapse remains | Scalability | **MODIFY** | 30 | Med | A |
| ER-26 — dev-port race (by design; kill-logic DRYed) | Correctness | **MODIFY** | 30 | Low | B |
| AC-132 — tray.py 881 LOC (marginal) | Maintainability | **MODIFY** | 30 | Low | A |
| GP-5 — caps_lock_suppressor keybd_event→SendInput | Correctness | **KEEP** | 30 | Low | G |
| FR-50 — blocking fs I/O in async cmds (export_data open) | Performance | **MODIFY** | 35 | Low | G |
| EC-25 — catch-all test files (all shrunk 18-22%) | Testing | **MODIFY** | 32 | Low | A |
| AC-139 — TS main-window/bootstrap/tcp-connect | Maintainability | **KEEP** | 35 | Low-Med | A |
| YJ-53 — ≥800 LOC umbrella list (3 items resolved) | Maintainability | **MODIFY** | 32 | Low | A |
| GQ-66 local builds / FR-57 dup noted above | — | — | — | — | — |

### Tier 4 — Low priority (10–29)

| Task Name | Category | Verdict | Score | Risk | Sub-Agent |
| :--- | :--- | :--- | :---: | :--- | :--- |
| XA-8 — ARIA gaps (4 of 7 fixed already) | Accessibility | **MODIFY** | 28 | Low | A |
| YJ-16 — dual Electron loggers | Maintainability | **KEEP** | 25 | Low | B |
| ER-39 — beam_size=1 default (intentional) | Performance | **MODIFY** | 25 | Nil | B |
| WR-9 — real_torch/network-egress fixed; monoliths grew | Testing | **MODIFY** | 25 | Low | B |
| AB-49 — 3 temp arrays in analyze_full_audio | Performance | **KEEP** | 30 | Low | B |
| AB-53 — load_binary_manifest uncached | Performance | **KEEP** | 25 | Low | B |
| AB-55 — dead elif in model_manager | Correctness | **KEEP** | 25 | Low | B |
| GQ-45 — .bak per-save (intentional durability) | Performance | **MODIFY** | 25 | E12 — do NOT remove | E |
| ZR-57 — conftest autouse fixture scope | Testing | **MODIFY** | 20 | Med | B |
| ZR-86 — ws.rs further split | Maintainability | **KEEP** | 25 | Low | B |
| NH-43 — bubble dismiss keyboard access (intentional overlay) | Accessibility | **KEEP** | 20 | UX tradeoff | B |
| FZ-57 — inline sys.platform checks | Maintainability | **MODIFY** | 20 | Low | B |
| FZ-58 — ticket-ID test class names | Style | **MODIFY** | 15 | Low | B |
| FZ-62 — setLocale missing from Tauri bridge | Parity | **KEEP** | 15 | Low | B |
| FZ-66 — 36 underscore test-only exports | Maintainability | **KEEP** | 15 | Low | B |
| AB-56 — try_load dead (60s wait gone) | Correctness | **MODIFY** | 15 | Low | B |
| FR-34 — tray dedup exists; per-title cap remains | Correctness | **MODIFY** | 20 | Low | G |
| AP-7 — dev-only URL scheme check | Security | **KEEP** | 20 | Nil | C |
| AP-12/26/32/48 — minor deferred items | Misc | **KEEP** | 18-22 | Nil | C |
| QV-27 — raw backend errors (arguably by design) | UX | **KEEP** | 25 | Nil | G |
| QV-13 — raw CAPS_LOCK labels | UX | **KEEP** | 20 | Low | G |
| GP-80 — registry comment 69 vs actual 73 | Docs | **MODIFY** | 15 | Nil | G |
| QV-106 — SUPPORTED_LOCALES non-alphabetic (safe cosmetic) | i18n | **KEEP** | 15 | Low | G |
| WR-10 — e2e naming cosmetic | Testing | **MODIFY** | 10 | Nil | B |
| ZR-49 — test naming conventions | Style | **KEEP** | 10 | Nil | B |
| ZU-19 — 17 local makeConfig files | Testing | **KEEP** | 10 | Nil | G |
| GQ-L appendix — 53 low items (most 0-3) | Perf nitpicks | mixed | 0-5 | Low | F |

**Constraint-trip screening: zero findings propose edits to tauri-*.yml workflows, action pins,
nuitka pin, artifact names, npm overrides, or Tauri config keys. Two findings (DT-41, DT-43)
would violate AGENTS.md design if implemented — scored 0.**

---

## Detailed Task-by-Task Breakdown

### Task: GP-44 — RPM depends on wrong webkit2gtk3 (CRITICAL)
- **Original claim:** rpm.depends lists `webkit2gtk3` (legacy 4.0 API) instead of `webkit2gtk4.1`; re-verified NOT FIXED 2026-08-12.
- **Sub-agent findings (G):** `src-tauri/tauri.conf.json:93` still contains `"webkit2gtk3"`; deb.depends at `:80` correctly uses `libwebkit2gtk-4.1-0`. Checked merge: `tauri.linux-x86_64.conf.json` overrides ONLY `bundle.targets` + `bundle.resources` — base conf drives the .rpm. Bug is ACTIVE.
- **Assumption check:** TRUE. Confirmed by orchestrator re-read.
- **Verdict:** `KEEP` · **Score:** `95`
- **Rationale:** Genuinely broken Linux RPM installs (Fedora 38+); fix is one line in a config file (not a protected workflow — C-CI-2 does not apply).
- **Action plan:** Edit `src-tauri/tauri.conf.json:93`: `"webkit2gtk3"` → `"webkit2gtk4.1"`. Run `cargo check` + existing tauri-conf tests.

### Task: GP-15 — wtype missing from deb/rpm depends (HIGH)
- **Original claim:** wtype used at runtime but absent from package depends.
- **Findings (G):** `voice_typer/server/clipboard/linux.py:382` `_have_wtype()`, `:393` `_linux_paste_via_wtype`, `:456` `cmd = ["wtype", ...]`. deb (`tauri.conf.json:77-84`) and rpm (`:90-97`) list wl-clipboard/xclip but NO wtype.
- **Assumption check:** TRUE.
- **Verdict:** `KEEP` · **Score:** `88`
- **Rationale:** Silent Wayland paste failure on clean installs of packaged builds.
- **Action plan:** Add `"wtype"` to both deb.depends and rpm.depends. Validate rpm package name `wtype` exists on Fedora.

### Task: TC-1 — pytest --dist=loadgroup configuration
- **Claim:** loadgroup configured; 13 xdist_group mentions, 5 real pytestmark decorators; counter-audit claiming zero decorators is FALSE.
- **Findings (E):** loadgroup live in Makefile:50,53,56 + build.yml:429; exactly 13 mentions / 5 real `pytestmark` decorators; "zero decorators" audit indeed false.
- **Assumption check:** TRUE.
- **Verdict:** `KEEP` · **Score:** `88`
- **Action plan:** Doc-level clarification; file-locks are the real guarantee. No behavior change.

### Task: EO-14 — HandlerBase._wrap defined but unused
- **Claim:** _wrap helper unused; 21 handler sites copy-paste 4-line validation boilerplate.
- **Findings (D/C):** `_wrap` at `handlers/_base.py:536` with **0 usage sites**; boilerplate actually grew to **26 sites across 10 handler files**.
- **Assumption check:** TRUE (count understated).
- **Verdict:** `KEEP` · **Score:** `82`
- **Rationale:** Mechanical, contained, immediate DRY win.
- **Action plan:** Migrate the 26 sites to `_wrap` (or delete `_wrap` if the abstraction is wrong); run handler tests.

### Task: TC-27 — time.time() wall-clock deadlines in tests
- **Claim:** 10 sites use wall clock for deadlines (NTP-jump flakiness).
- **Findings (E):** Actually **36 sites / 17 files** (undercounted); 2 already migrated to monotonic.
- **Verdict:** `KEEP` · **Score:** `80`
- **Action plan:** Mechanical `time.monotonic()` swap, carving out legitimate timestamp uses.

### Task: VP-40 — CrashRecovery.__del__ is 102 lines
- **Findings (D):** `__del__` at crash_recovery.py:1137-1239 ≈102 LOC — exact.
- **Verdict:** `KEEP` · **Score:** `80`
- **Rationale:** Textbook extraction; `__del__` with 100+ lines of IO is a GC-time liability.
- **Action plan:** Extract `close()`/cleanup helpers; `__del__` becomes a 3-line delegate.

### Task: EO-13 / AC-73 — orchestrator.run() 453-LOC god-method (duplicate pair)
- **Findings (A/D):** orchestrator.py 641 LOC; run() spans 453 lines with a 195-line finally block.
- **Verdict:** `KEEP` · **Score:** `70`
- **Rationale:** Hottest correctness path; bounded extraction, high value.
- **Action plan:** Extract the finally's 7 cleanup steps into named helpers (each owning try/except); StageTimer context manager; behavioral tests for abort/cancel/device-loss first.

### Task: FI-S10 — config_validators/__init__.py split
- **Findings (D):** 862 LOC currently; extraction into allowlist.py + entry_points.py is Effort-S.
- **Verdict:** `KEEP` · **Score:** `75`
- **Constraint note:** SEC-002 allowlist import path must stay compatible (`voice_typer/server/config_validators/__init__.py` re-exports).

### Task: VP-38 — startup_sequence.run 829-line god-method
- **Findings (D):** now ≈925 LOC (WORSE than claimed); class is effectively 2 methods.
- **Verdict:** `KEEP` · **Score:** `75`
- **Action plan:** Extract phased sub-runs (each returning a stage-result); keep log-line text identical (C-LOG-1/2).

### Task: TC-43 — @types/node ^26 vs Node 24 runtime
- **Findings (E):** package.json:89 `@types/node@^26.1.1`; engines `>=24`; CI on Node 24 (one stray node-version:22 at build.yml:577).
- **Verdict:** `KEEP` · **Score:** `72`
- **Action plan:** Downgrade types to `^24`; never raise engines; fix the stray :577 node-version as separate hygiene.

### Task: VP-36 — config_path_safety.py half-done shim
- **Findings (D):** 75-line pure re-export shim, self-documented.
- **Verdict:** `KEEP` · **Score:** `72`
- **Action plan:** Move bodies into canonical modules; keep shim for test-patch compat until ARCH-9-style migration.

### Task: EO-15 / VP-37 — clipboard paste() 542-LOC god-method (duplicate pair)
- **Findings (D):** paste() 451-993 ≈542 LOC — exact; `_is_safe_paste_target` extraction landed via clipboard_target_safety/safety.py; VP-37's "dead code" sub-claim FALSE.
- **Verdict:** `MODIFY` · **Score:** `72`
- **Rationale:** Safety-critical paste path; split must preserve ordering guarantees.
- **Action plan:** Extract paste stages (target-check → payload-fetch → injection → restore) with explicit error contracts; existing tests green before/after.

### Task: GQ-42 — microphone watcher dead idle state
- **Findings (E):** `set_idle` wired (:177-216) and consumed (:551, :718) but **zero production callers** — `_is_idle` stuck True; effect INVERTED (active 3s cadence never engages during recording).
- **Verdict:** `MODIFY` · **Score:** `65`
- **Action plan:** Wire set_idle from Recorder start/stop, or delete the dead branch — decide based on intended cadence; currently the watcher idles at 12s always.

### Task: SI-17 — duplicated PROTOCOL_VERSION (duplicate with UE-26)
- **Findings (C):** Both constants exact at cited lines; TCP rejects structured (transport_tcp.py:505-543); WS advisory-only (sidecar_ws.py:915-931, documented S1-CR-78 design). All 4 surfaces = 1; parity test passes; `test_app_sidecar_protocol.py:95` pins ==1. Key new fact: **Electron main never sends protocol_version → TCP rejection is currently dead code.**
- **Verdict:** `MODIFY` · **Score:** `62` (UE-26: 48 — bump is cosmetic until clients emit version)
- **Action plan:** Consolidate constants into `ipc/protocol_version.py` keeping WS behavior as documented; do NOT bump to 2 without a cross-language parity update + client emission.

### Task: EO-1 — app.py __init__ god-constructor (sub-task of HU-44)
- **Findings (D):** app.py 1676 LOC; __init__ ≈630 lines (310→939) — worse than claimed 592.
- **Verdict:** `KEEP` · **Score:** `65` (parent HU-44 full split: 42)
- **Action plan (HU-44/GQ-26):** First win = lazy_property descriptor for the ~12 property pairs (~150 LOC saved); re-export shims → _reexports.py; full app/ package split multi-day.

### Task: EO-3 / FI-S4 / GQ-24 — sidecar_ws.py monolith (triple duplicate)
- **Findings (D):** 1827 LOC (not 2027), no package split; core IPC transport across 8+ concerns.
- **Verdict:** `KEEP` · **Score:** `65`
- **Rationale:** Biggest Python monolith; but highest-risk split — schedule with full test coverage gate.

### Task: EO-12 / AC-131 / FI-S3 — config package stalled split (duplicates)
- **Findings:** 2389 LOC (not 2613); partial split landed; 21 classmethod-delegator wrappers.
- **Verdict:** `KEEP` · **Score:** `60`
- **Action plan:** Land config_dataclass/config_saver/config_purge; delete test-only classmethods after migrating patch sites; keep SEC-002 allowlist chain.

### Task: FI-S2 / AC-128 / GQ-70 — credential_store.py (duplicates)
- **Findings:** 1931 LOC (not 2132); no split; 22 module functions + 11 globals.
- **Verdict:** `KEEP` · **Score:** `60`
- **Action plan:** Package split per entry plan; preserve O_NOFOLLOW + redaction + keyring-probe behavior exactly.

### Task: FI-S1 — history_db.py partial split completion
- **Findings (D):** 2255 LOC (not 2529); history_db_internals/ (2929 LOC pkg) exists.
- **Verdict:** `KEEP` · **Score:** `60`
- **Note:** WM-10 correction stands — search.py is LIVE (655 LOC, 10 import sites); must NOT be deleted.

### Task: FR-44 — Rust RotatingFileWriter Mutex across blocking I/O
- **Findings (G):** logging.rs:1537-1555 `Mutex<Option<BufWriter<File>>>` held across write_all/flush/set_len/seek in write_line (:1571-1727).
- **Verdict:** `KEEP` · **Score:** `60`
- **Action plan:** Background writer thread with channel; C-LOG-1 format must not change.

### Task: FR-52 — bare dict/list annotations
- **Findings (G):** config_applier.py bare `dict` at 15 sites; providers.py ServiceProtocol bare dict/list at :411-479.
- **Verdict:** `KEEP` · **Score:** `55`
- **Action plan:** TypedDict for config updates; verify IPC parity tests unaffected.

### Task: IN-3 — lazy-property retry sentinel
- **Findings (C):** None-guards landed (crash fixed) but `_LAZY_FAILED` sentinel NOT implemented; hot path still re-attempts construction + logs ~2 WARNINGs/chunk on failure.
- **Verdict:** `KEEP` · **Score:** `60`
- **Action plan:** Cache failure sentinel with bounded TTL for the 6 lazy properties; consider eager audio_quality construction.

### Task: AP-10 / AP-47 — log.exception PII + log.error→log.exception migration
- **Findings (C):** log.exception = 147 callsites/58 files (near-exact); log.error = 213 sites/85 files.
- **Verdict:** `KEEP` · **Score:** `55` each
- **Action plan:** Chip-away waves with redaction-aware templates.

### Task: WM-9 — _WRITE_FUTURE_TOTAL_TIMEOUT dead
- **Findings (G):** Defined history_db.py:85 (=60.0); referenced ONLY by tests that assert existence; writer loop is `while True` with 30s per-retry.
- **Verdict:** `KEEP` · **Score:** `55`
- **Action plan:** Wire the 60s deadline into the writer loop; do NOT delete the constant (test pins ==60.0).

### Task: SI-25 / VP-30 — state.rs split (duplicates)
- **Findings (C):** 898 LOC; SidecarHandle + shutdown machinery + WorkerState(:433) mixed.
- **Verdict:** `KEEP` · **Score:** `55`
- **Action plan:** sidecar/handle.rs + sidecar/shutdown.rs; cargo check gate.

### Task: GQ-11 — platform/logging.rs 7-file split
- **Findings (D):** 1677 LOC; inline tests already in logging_tests.rs (89 tests) — C-TEST-5 satisfied.
- **Verdict:** `KEEP` · **Score:** `55`
- **Constraint:** C-LOG-1 pins file/time formats — split moves code, never changes templates.

### Task: AC-66 — app private state accessed by 7 modules
- **Findings (A):** WORSE than claimed: 7 modules/~21 sites incl. new recording_lifecycle.py + transcription_watchdog.py.
- **Verdict:** `MODIFY` · **Score:** `52`
- **Action plan:** BusynessCoordinator owning _busy_event/_lock with is_busy()/set_busy()/set_idle(); migrate one module at a time.

### Task: XZ-R11-04 — no encryption at rest for dictated text
- **Findings (A):** TRUE; history DB plaintext + crash recovery plaintext; 0o600 perms + secure_delete only.
- **Verdict:** `KEEP` · **Score:** `50`
- **Rationale:** Privacy gap (Windows perms weaker); opt-in SQLCipher or app-layer encryption keyed in OS keystore.

### Task: S1-CR-67 / ZR-38 / DT-38 — sys.modules test-patch shims (triple duplicate)
- **Findings (A):** _RecordingModule class exists (recording/__init__.py:425); _pkg.X indirection in all 3 packages; total 907 LOC (not ~2000).
- **Verdict:** `MODIFY` · **Score:** `50`
- **Action plan:** Migrate tests to patch submodules directly; delete custom module class + indirection.

### Task: ARCH-12 / S3-CR-21 / FZ-8 — inspect.getsource tests (triple duplicate)
- **Findings (A):** 467 calls / 149 files (claim 478/150 — near-exact).
- **Verdict:** `MODIFY` · **Score:** `45`
- **Action plan:** Adopt "no new source-pinning tests" rule; port when touching pinned modules; ast.walk where structure genuinely needed.

### Task: ARCH-9 — app.py test-seam re-exports
- **Findings (A):** Re-exports confirmed; 205 live monkeypatch sites (not 218).
- **Verdict:** `MODIFY` · **Score:** `42`
- **Action plan:** Batch-migrate by symbol (top 4 autostart/microphone symbols first); then delete re-export blocks.

### Task: XA-5 / XA-2 — UX friction & pattern inconsistency
- **Findings (A):** XA-5: 22/23 sub-items live (sub-item 16 FIXED); XA-2: all three divergences confirmed live.
- **Verdicts:** `MODIFY` 45 / `KEEP` 35
- **Constraint:** C-I18N-1 (all 8 locales) for any new strings; C-BRAND-1 placeholders.

### Task: TEST-2 — time.sleep in tests
- **Findings (A):** 410 calls / 151 files (claim 495/239 overstated).
- **Verdict:** `MODIFY` · **Score:** `38`

### Task: EO-17 / CSTYLE-1 — task-ID comment sweep
- **Findings (C/D):** XZ- occurrences down to 397/88 files (claim 830/530 halved by prior sweeps); widest regex 4085 hits. ~8 non-sanctioned IDs in 7 production files (not 60+); PERF-*/SEC-*/RACE-* are SANCTIONED; tauri-*.yml evidence tags off-limits (C-CI-2); `test_dead_code_stays_removed.py:777-869` greps source for IDs — a blind sweep breaks it.
- **Verdict:** `MODIFY` · **Score:** `45`
- **Action plan:** Targeted sweep with carve-outs + update source-grepping tests in the same change; optional CI lint afterward.

### Task: EO-19 — 4 platform files >800 LOC
- **Findings (D):** crash_recovery.py 1176 (exact-ish), autostart_windows.py 1485, startup_sequence.py 1246, autostart_launcher.py 1232; autostart_windows mechanism split already partially done via _pkg delegation. All C-CROSS pins verified intact.
- **Verdict:** `MODIFY` · **Score:** `60`

### Task: ER-series (B slice) highlights
- **ER-2 (45 MODIFY):** noisy_room DOES get real RNNoise (init-time fallback at noise_suppressor.py:257-356); only DFN enhance() missing. Premise half-stale.
- **ER-35 (40 KEEP):** double-emit real (ws.rs:805-806) but intentional per ADR-0020, pinned by tests — keep unless perf data changes.
- **ER-39 (25 MODIFY):** beam_size=1 is an intentional speed default, user-configurable.
- **ER-48 (45 KEEP):** no model fence after force_recover — real race.
- **AB-55 (25 KEEP):** dead elif confirmed model_manager.py:1461.
- **YJ-15 + FZ-27 (40 KEEP, pair them):** VoiceTyperError enum absent repo-wide; 34+ `Result<_,String>` sites; thiserror declared in Cargo.toml:74, zero usage. One combined migration.

### Task: FR remaining open list
- **FR-44 (55), FR-50 (35 MODIFY — open_logs already spawn_blocking; export_data still sync), FR-52 (55), FR-34 (20 MODIFY — dedup EXISTS; only per-title cap gap), FR-57 (30 — DUPLICATE of WM-2/VP-24).**
- FR-4/10/14/51/54: the 4 claimed "missing test files" are confirmed genuinely absent — status ACCURATE; FR-10's code anchor (prewarm_scheduler_posix.py) is deleted → re-anchor to server_platform/autostart_linux.py. Score 40 (tests must be written to match landed fixes).
- FR-7/11/26/40/49: unverified premises (Medium/Low severities as filed) — scores 25/25/15/20/15.

### Task: GQ-L low-severity appendix (53 items)
- **REAL & present:** 46 items (mostly genuine sub-50ms micro-perf / style nits; scores 0-5 per appendix guidance "do NOT spend a dedicated sub-agent").
- **FALSE / ALREADY-FIXED (5):** GQ-L26 (`_transcribe_segment_unlocked` doesn't exist), GQ-L39 (`shareAsImage` doesn't exist — hallucinated), GQ-L59 (no naming collision), GQ-L28 (PENDING_MAX=1024 admission gate exists), GQ-L25 (parakeet 1011 LOC, not 1577).
- **CONSTRAINT-TRIPS if "fixed":** GQ-L14 (PortAudio copy required), GQ-L21 (secure_delete=ON is privacy), GQ-L35 (sync write = crash durability), GQ-L50 (fat-LTO = release quality), GQ-L51 (per-arch conf = C-CI-10/XS-28 design). All REJECT.
- **Best of the appendix:** GQ-L48 remove unused `@vitest/coverage-v8` (istanbul provider configured — safe), GQ-L49 drop unused config-json5 Cargo feature, GQ-L2 module-level regex hoist.

### Task: ZU remaining work
- **ZU-19 (10 KEEP):** exactly 17 local makeConfig files — lint tracks it.

### Task: Cannot-verify / host-blocked
- **XPLAT-12, S1-CR-146, GQ-41, GQ-54, WM-6..14, GP-7/66/70/135, VT-1, ZU-46, FR-42/43/45, GG-72:** genuinely host-blocked (Windows/macOS/Linux-desktop runtimes or runner availability). KEEP as tracked validation checklist; score 0 actionable.
- **R2-1 (meta, N/A):** all referenced files exist; status consistent with host-validations-remaining.

---

## Aggregate Statistics
- **Findings audited:** ~150 headings → **~118 unique issues** after dedup (≈32 are duplicate filings:
  ARCH-12≡S3-CR-21≡FZ-8; S1-CR-67≡ZR-38≡DT-38; EO-13≡AC-73; XS-42≡SI-29; AC-128≡GQ-70;
  AC-131≡EO-12; AC-134≡EO-4≡GQ-25; AC-136≡GQ-28; AC-137≡GQ-31; HU-44≡VP-24≡GQ-26≡WM-2≡FR-57;
  EO-3≡FI-S4≡GQ-24; SI-25≡VP-30; ZR-86≡UE-30≡GQ-53; ER-93≡FZ-60; FI-S block printed twice).
- **Verdicts:** KEEP 68 · MODIFY 34.
- **Top quick wins (do first):** GP-44 (1 line), GP-15 (2 lines), TC-27, EO-14, VP-40, AB-55 dead-elif removal, GP-80 comment (→73), GQ-L48 dep removal.
- **Recommended next wave (70-79):** FI-S10, VP-38, EO-13, TC-43, VP-36, EO-15.
- **Big-ticket architecture (schedule as big-tasks per E16):** sidecar_ws.py (1827), config/__init__.py (2389), model_manager.py (2494), recorder.py (2584), history_db.py (2255), credential_store.py (1931) — each gated by existing test suites + create-first split rule (E1).
- **Zero proposed fixes require touching protected surfaces** (tauri-*.yml, action pins, nuitka pin, artifact names, npm overrides, vitest pool, pytest import-mode).

*Full per-inspector evidence files: `%TEMP%\opencode\audit_inspector{A..G}.md`*
