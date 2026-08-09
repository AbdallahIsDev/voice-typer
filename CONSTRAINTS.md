# CONSTRAINTS.md — Hard "Don'ts" (HIGHEST PRIORITY)

> This file is the **single source of truth for things the agents must NOT do**, even when those things would "improve" the project. Every rule here is a HARD CONSTRAINT that overrides:
> - `PROMPT.md` (cloud agent) — including `## Current Tasks`, `## Execution TODOs`, `review.md` entries, and any "would-improve" idea.
> - `MERGE-SESSIONS.md` (cloud merge agent) — including "the better-implemented version wins".
> - `VERIFY.md` (local verifier) — the verifier flags any change that violates a rule here.
> - `TRIVIAL-FIXES.md`, `SERIOUS-FIXES.md`, `PUSH.md` (local fixer / documenter / committer) — all respect these rules.
> - Every sub-agent launched by the orchestrator — the orchestrator MUST embed the relevant rules into each sub-agent's prompt.
>
> If a `review.md` task, a sub-agent finding, or an "improvement" idea conflicts with a rule here, the agent MUST SKIP the work and record the skip in `worklog.md` with the conflicting rule cited. CONSTRAINTS.md is the ONLY file that can forbid work that would otherwise look like an improvement.
>
> **The user is the only one who can edit this file.** Agents must NOT add, modify, or delete rules here. If an agent believes a rule should be added or removed, it should RECOMMEND the change in `worklog.md` (or in the chat report) and let the user decide.

---

## Constraint categories

Constraints below are organized by category. Each constraint has:
- **ID** (e.g. `C-TRAY-1`) — for citing in `worklog.md` skip-reasons.
- **Rule** — the prohibition, stated clearly.
- **Rationale** — why this rule exists (so the agent understands it's not arbitrary).
- **Applies to** — which agents / modes the constraint affects.

---

## Category: Tray Menu & Application Close

```
C-TRAY-1
Rule: Do NOT add a "Repaste Last transcription" button to the tray menu.
Rationale: The tray menu is intentionally minimal;
Applies to: All agents, all modes.
```

---

## Category: UI & UX


---

## Category: Localization & i18n

```
C-I18N-1
Rule: Do NOT add any user-facing text (UI labels, buttons, tooltips, dialogs, notifications, tray menu, errors) without adding it to ALL locale files. User-visible strings MUST go through the i18n layer (`useT()` / `t()` in the renderer, `mainT()` in the main process) and the new key MUST be added to every locale under `voice_typer/client/src/renderer/src/i18n/translations/`: `en.json`, `ar.json`, `de.json`, `es.json`, `fr.json`, `hi.json`, `ru.json`, `zh.json`. Adding the key to `en.json` only, or to a subset of locales, is a violation — every locale must contain the key (see `SUPPORTED_LOCALES` in `i18n/locale.ts`).
Rationale: The app is multilingual (8 locales). A key missing from a locale file means users of that language fall back to English (or see raw keys) — a silent downgrade that is invisible when only English is tested.
Applies to: All agents, all modes.
```

```
C-I18N-2
Rule: Do NOT copy the English source text verbatim into a non-English locale file. Every non-English locale value MUST be genuinely translated into that language (e.g. `ar.json` values must be Arabic, `fr.json` values must be French — not English text pasted under the key). If the agent cannot translate reliably, it MUST translate via a translation tool/LLM or record the entry as pending in `worklog.md` (with the key name) so a later session completes it — never ship untranslated English inside a non-English locale.
Rationale: Hardcoded English inside e.g. `ar.json` defeats the multilingual promise: Arabic users see English strings. This is the #1 observed i18n downgrade — the key exists in every locale file, so missing-key tooling won't catch it, only the translation itself is wrong.
Applies to: All agents, all modes.
```

---

## Category: Branding

```
C-BRAND-1
Rule: Do NOT hardcode the app-name display string anywhere — always use the dynamic branding constant: Python `APP_NAME` (`voice_typer/server/branding.py`), TS main `APP_NAME` (`src/main/branding.ts`), TS renderer `APP_NAME` (`src/renderer/src/branding.ts`), Rust `crate::branding::APP_NAME`. Locale files (BOTH `renderer/src/i18n/translations/*.json` AND `main/i18n/locales/*.json`) MUST use the `{appName}` placeholder token (runtime-substituted, as `_withAppName` in `main/i18n.ts` already does) — never a literal brand string, not even in `en.json`. Prose comments describing the app must also avoid the literal brand. This does NOT apply to internal identifiers (types like `VoiceTyperConfig`, mutex/binary names like `VoiceTyperSingleInstance` / `VoiceTyper.exe`) — those are OS/API identifiers, not the user-facing brand, and must not be renamed.
Rationale: An agent hardcoded the brand inside locale files (dozens of literal strings across all 8 `i18n/translations/*.json`) plus crash-dialog titles, HTML `<title>` tags, and backend error messages. `scripts/check_branding.py` (BRAND-001) deliberately EXEMPTS renderer translations and comment lines, so those literals bypass CI enforcement — a future product rename becomes a hundreds-of-strings edit instead of a one-constant change. The `{appName}` placeholder pattern already exists in main-process locales; renderer locales must adopt the same pattern.
Applies to: All agents, all modes. Enforced in CI by `scripts/check_branding.py` for non-locale, non-comment code.
```

---

## Category: IPC & Command Surface


---

## Category: Architecture & Module Boundaries

```
C-ARCH-1
Rule: `src-tauri/src/main.rs` MUST stay wiring-only (≤ ~300 lines). Do NOT add implementation logic to `main.rs` — even if a task asks for it. Logic goes in focused modules under `src-tauri/src/`.
Rationale: Rule 19 in PROMPT.md; prevents the 2277-line spaghetti regression.
Applies to: All agents, all modes.
```

---

## Category: Cross-Platform Behavior


---

## Category: Dependencies & Supply Chain


---

## Category: CI/CD & Build Pipeline

```
C-CI-1
Rule: Do NOT unpin GitHub Actions versions. All actions must be pinned to specific Node-24-runtime versions (see the header of `build.yml`). Unpinning introduces supply-chain risk via tag re-pointing.
Rationale: Security — pinned versions prevent a compromised action update from silently breaking CI or exfiltrating secrets.
Applies to: All agents, all modes. Especially relevant to IMPROVE mode targeting Group 6 (Testing & CI).
```

---

## Category: Data & Privacy

```
C-DATA-1
Rule: Do NOT add network calls to the production code path UNLESS they fall into an explicitly allowed category: (1) cloud transcription / LLM providers the USER has configured and consented to (openai / groq / deepgram / custom `cloud_api_url` — see `cloud_engines.py` / `llm_polish.py`); (2) auto-update — "Check for Updates" / silent update check against the GitHub API (see `docs/auto-update-feature.md`); (3) model downloads (see ADR-0005, `docs/adr/0009-audio-filter-chain-architecture.md`). Anything NOT user-configured and user-initiated — telemetry, analytics, tracking, phone-home, or any other unsolicited egress — remains forbidden.
Rationale: The original wording ("no network call ever") predates cloud ASR/LLM engines and the auto-update feature, so agents applied it as a blanket offline ban and downgraded legitimate user-initiated features (e.g. "Check for Updates" CSP, cloud provider calls). The product promise is "no unsolicited phone-home", NOT "no network access ever". Agents that previously skipped, removed, or reworked network functionality citing the old wording MUST re-audit that work (search `worklog.md` / `review.md` for C-DATA-1 skips) and restore or improve anything that was downgraded.
Applies to: All agents, all modes.
```

---

## Category: Testing & Baselines

```
C-TEST-1
Rule: Do NOT revert the Vitest `pool: "threads"` setting in `voice_typer/client/vitest.config.ts`. The fork pool (default) creates a new child process per test file (~200ms overhead × 237 files = ~47s wasted). Threads share memory and eliminate this overhead, cutting Vitest suite time by ~2.5x (measured: 66s → 25s).
Rationale: On 2026-08-02, `isolate: false` was also tested alongside `pool: "threads"` but was reverted — it caused 22 additional test failures from Zustand store / localStorage mock state leaking between test files. The `pool: "threads"` change alone is safe and provides the bulk of the speedup. Do NOT remove or change `pool` back to the default ("forks").
Applies to: All agents, all modes. Especially relevant to IMPROVE mode targeting Group 6 (Testing & CI).
```

```
C-TEST-2
Rule: Do NOT remove `--import-mode=importlib` from `[tool.pytest.ini_options].addopts` in `pyproject.toml`. The default `prepend` import mode copies every test file to a temp directory and adjusts `sys.path` per file, adding ~1-2s of I/O overhead at collection time on a 544-file suite. `importlib` mode uses `importlib.import_module()` directly — faster and avoids subtle `__file__` / `__name__` mismatches.
Rationale: Added as part of PERF-007 test suite optimization on 2026-08-02. Verified safe — all existing tests pass with `importlib` mode. The `norecursedirs` entry in the same section prevents pytest from crawling `.venv/`, `node_modules/`, `.git/`, etc. during collection.
Applies to: All agents, all modes. Especially relevant to IMPROVE mode targeting Group 6 (Testing & CI).
```

```
C-TEST-3
Rule: Do NOT remove `pytest-xdist` (`-n auto --dist=loadgroup`) from local `make test` and CI pytest invocations. `pytest-xdist` parallelizes test execution across all available CPU cores, providing ~2-3x speedup on multi-core machines. The package is already declared in `[project.optional-dependencies].test` and CI's `build.yml` installs it explicitly.
Rationale: Added as part of PERF-007 on 2026-08-02. The Makefile's `test` target now uses `-n auto --dist=loadgroup` (previously single-threaded). CI already used `-n auto` but the local `make test` did not — contributors running `make test` were not getting the parallelism benefit.
Applies to: All agents, all modes. Especially relevant to IMPROVE mode targeting Group 6 (Testing & CI).
```

```
C-TEST-4
Rule: Do NOT add `--cov` or `--coverage` flags to local test runs (Makefile `test-client`, individual `pytest` invocations). Coverage instrumentation adds 15-25% overhead. Use `--no-coverage` for local dev; CI (`build.yml`) and pre-push hooks are the correct places for coverage enforcement.
Rationale: Added as part of PERF-007 on 2026-08-02. The Makefile `test-client` target now passes `--no-coverage` to Vitest. The Makefile `test` target does not pass `--cov` (it relies on `addopts` which includes `--cov`, but local devs can override with `--no-cov`). CI explicitly passes `--cov` and `--cov-fail-under=65` in its own step.
Applies to: All agents, all modes. Especially relevant to IMPROVE mode targeting Group 6 (Testing & CI).
```

```
C-TEST-5
Rule: Do NOT put test code inside production source files. Tests MUST live in separate test files/folders, for every language: Python → `tests/`; Rust → a sibling `tests.rs` module wired via `#[cfg(test)] mod tests;` (or `src-tauri/tests/` integration tests); frontend → `*.test.ts` / `*.spec.ts` files (or the renderer `__tests__/` convention). No inline `#[cfg(test)] mod tests` blocks in `.rs` source files, no test assertions inside Python modules, no test cases inside production TS/TSX. When splitting a module, new tests for it go in the module's separate test file — never appended inline to the production file.
Rationale: Inline tests bloat production files and mix concerns — `src-tauri/src/platform/logging.rs` carried 89 `#[test]` fns inside a 3183-line production file, and split sessions silently lost or mis-wired inline test blocks. The repo's own conventions already separate tests everywhere else (Python `tests/`, bubble `tests.rs`, renderer `__tests__/`); inline Rust tests were the remaining inconsistency.
Applies to: All agents, all modes, all sub-agents.
```
---

## Category: Code Style & Naming

```
C-STYLE-1
Rule: Do NOT add task IDs, session prefixes, or ticket numbers to source code (file names, function names, class names, variable names, comments). The session prefix (e.g. `CR`, `X7`) belongs ONLY in metadata files (`review.md`, `SUMMARY.md`, `worklog.md`). This is also enforced as Rule 21h in PROMPT.md and pattern M12 in VERIFY.md — but it is a CONSTRAINT here because agents repeatedly violate it.
Rationale: Task IDs are transient; a future session has a different prefix. Code named after a task ID becomes meaningless noise once the entry is removed from `review.md`.
Applies to: All agents, all modes, all sub-agents. THE ORCHESTRATOR MUST EMBED THIS RULE IN EVERY SUB-AGENT'S PROMPT.
```

---

## Category: Tauri Config

```
C-TAURI-1
Rule: Do NOT use Tauri v1 config keys in `tauri.conf.json`. The project uses Tauri v2 (schema URL: `https://schema.tauri.app/config/2`). V1 keys (`postInstall`, `preRemove`) must be renamed to their v2 equivalents (`postInstallScript`, `preRemoveScript`). Reverting to v1 keys will break the Tauri build.
Rationale: The Tauri build process reads tauri.conf.json and fails on unrecognized v1 key names. The project already migrated to v2 keys; reverting introduces a build blocker.
Applies to: All agents, all modes. Especially relevant to IMPROVE mode targeting Group 1 (Architecture) or Group 6 (Testing & CI).
```

---

## Category: Logging & Observability

```
C-LOG-1
Rule: Do NOT change the canonical log line template. FILE lines MUST be exactly `YYYY-MM-DD  HH:MM:SS  LEVEL  msg` — TWO spaces between the date and the time, seconds-only precision (NO millisecond fraction), no `T` separator, no timezone offset, short level labels (`DEBUG` / `INFO` / `WARN` / `ERROR` / `CRITICAL` — `WARN`, never `WARNING`), and NO per-line session id, thread name, function name, or module/component path. The ONLY sanctioned occurrence of the session id is the trailing `session=xxxxxxxx` field on the FIRST line of the session — the `[STARTUP] logging initialized:` banner emitted by `voice_typer/server/logging_setup.py` (the banner is logged once per process with the 8-char hex id returned by `log.setup_logging`); it must NEVER appear on any other line. This keeps the session mechanism alive and greppable (`session=`) while keeping every other line clean. TERMINAL (stderr) lines MUST be `HH:MM:SS  LEVEL  msg` — TIME ONLY, no date (the date lives only in the log file; console output shows just the clock). This applies to BOTH the Python formatters (`_iso_timestamp`, `_FileFormatter`, `_ColorFormatter` in `voice_typer/server/log/formatters.py`) AND the Rust sidecar (`now_timestamp` / `now_time_only` / `now_timestamps` in `src-tauri/src/util.rs`, `CombinedLogger` / `EarlyLogger` in `src-tauri/src/platform/logging.rs`). The ONLY sanctioned exception is the opt-in JSON formatter (`VOICE_TYPER_LOG_JSON=1`), which keeps the UTC `Z` + millisecond timestamp for log aggregators.
Rationale: The legacy format rendered `2026-08-06T23:14:33.352+0300 [2ae8edcc] [MainThread] INFO [voice_typer.server.logging_setup] ...` — ISO-8601 with tz offset + millis, a per-process session id, thread name, and module path on EVERY line, plus the long-form `WARNING` label, all of which made the log unreadable. The clean template above replaced it on 2026-08-08 and is pinned by `tests/test_logging.py` (`_EXACT_FILE_LINE_RE`), `tests/test_log_formatting.py` (`_TS_RE_TEXT` / `_TS_RE_TERM`), and `src-tauri/src/util_tests.rs` (`test_now_timestamp_format` / `test_now_time_only_format` / `test_now_timestamps_pair_consistent`). Reverting ANY part of it — re-adding millis, `T`/tz, session id, thread, module path, the `WARNING` label, or moving the date to the terminal / removing it from the file — regresses the readability fix. When touching logging, keep the format unchanged and update these tests if a (user-approved) format change is ever made.
Applies to: All agents, all modes, all sub-agents.
```

```
C-LOG-2
Rule: Do NOT remove the `_<duration>` suffix from lifecycle-completion log lines. Every log line that reports the END of a timed operation (startup complete, model/VAD/CUDA-DLL load, warm-up inference, transcription, recording stop, and any future timed load/transaction) MUST carry a duration suffix produced by `format_duration()` in `voice_typer/server/duration.py` (dependency-free; import it rather than inlining ad-hoc `f"..._{x:.1f}s"` strings that drift from the minutes case): `_2.3s` for sub-minute durations, `_1m 2.3s` for anything longer. Never bare `2.3s`, never `took=2.3s`, never `-- 2.3s` — the underscore form is the canonical, greppable performance marker. The suffix is attached to the timed event, normally at line END; the recording line is the one intentional mid-line placement (`Recording stopped _30.0s of audio, ...` reads naturally — the duration IS the subject). Timed lines today: `[STARTUP] Startup complete (model still loading in background)_3.7s`, `[MODEL] Model loaded via ... _1.4s`, `[PERF] Warm-up inference completed — CUDA kernels primed_2.4s`, `[CUDA-DLL] Prepended to PATH: [...]_0.8s`, `[TRANSCRIBE] Transcription complete (len=..., cycle=...)_0.8s`, `[DICTATION] Recording stopped _30.0s of audio, ...`, `[VAD] Silero VAD model preloaded + warmed_1.2s`. The measurement source is always `time.perf_counter()` (monotonic) captured at the start of the operation and diffed at the completion log. Grep anchor: `_\d+(m \d+)?\.\ds`.
Rationale: Added 2026-08-08 so performance is measurable at a glance in the log file — how long startup took, how long the model/packages took to load, how long transcription took, and how long the user recorded. Prior to this, several completion lines had no duration at all (startup) or used inconsistent ad-hoc formats (`took=%.1fs`, `— %.1fs`, `-- %.1fs of audio`) that could not be grep-summed. Reverting to a duration-less or non-underscore format regresses the performance observability the user explicitly requested.
Applies to: All agents, all modes, all sub-agents.
```

---

## How the adds / edits constraints

1. Add a new constraint block under the appropriate category (or create a new category with a `## Category: <name>` header).
2. Fill in the `Rule`, `Rationale`, and `Applies to` fields.
3. Save. The next cloud session / local agent run will read the updated file at start.

**Template for a new constraint:**
```
C-<CATEGORY>-<N>
Rule: <one-sentence prohibition, starting with "Do NOT...">
Rationale: <why this rule exists — 1-2 sentences>
Applies to: <all agents / specific agents / specific modes>
```

---

## Audit trail (when constraints are cited)

When an agent skips work due to a constraint, the skip is recorded in `worklog.md` (cloud agent) or in the chat report (local agent) with the format:

```
SKIPPED: <task ID or finding ID> — conflicts with CONSTRAINTS.md: <C-ID> (<one-line rule summary>)
```

The user can `grep` `worklog.md` for `SKIPPED:` to see every constraint-driven skip across sessions. This audit trail is essential for understanding why work was deferred — and for deciding whether a constraint should be relaxed in the future.

---

## Final note

This file is intentionally spare — the user fills it in over time as they discover areas where the cloud agent's "improvements" would damage the project's intent. Every rule here was added because a cloud agent (or a session in a merge) previously did the prohibited thing and the user had to revert it. Adding a rule here prevents the next agent from repeating the mistake.
