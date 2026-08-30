## AGENTS.md read confirmation (breadline first)

This file has been read entirely by the agent at session start. When the user gives a task in a fresh session WITHOUT explicitly asking you to read AGENTS.md, your very first reply line MUST be exactly:

`AGENTS.md file has been read successfully.`

Then continue with the normal response to the user's request (status, findings, plan, questions — whatever the task calls for). This one line is the user's proof that you loaded this file on your own, without being told.

If you did NOT read this file (it was not injected/loaded, or you skipped it), say NOTHING about it — never claim you read it, never mention the file at all. The user will know from the absence of the line.

If this file does NOT exist in the repository root / project folder, tell the user exactly:

`AGENTS.md file is not found in this repository. Would you like me to create it with some strict instructions?`

If the user says yes, create it and offer to fill it with strict, binding rules the agent (and every future agent) must follow without deviation — unbreakable rules the user can enforce across sessions (e.g. hard "do nots", allowed/forbidden actions, required behaviors). Nothing in the file may be overridden or bypassed by the agent.

Default response style: caveman full.
Use terse, token-efficient replies by default: no filler, no pleasantries, fragments OK, technical terms exact.
Keep code blocks, commands, commit messages, PR text, and exact error quotes normal and precise.
Use fuller clarity for security warnings, irreversible actions, or multi-step instructions where compression could confuse.
Exit this style only when the user says `normal mode` or `stop caveman`.

# Project-level agent instructions for voice-typer

## Branding — DO NOT HARDCODE APP NAME

**CRITICAL RULE:** Never replace `APP_NAME` usages with the hardcoded string
"Voice Typer" (or "VoiceTyper") anywhere in the codebase. Even though
`APP_NAME` currently resolves to "Voice Typer", the VARIABLE exists so the
app name can be changed in ONE place and propagate everywhere automatically.

If you are an AI agent and feel tempted to inline the value — **DON'T**.

- **Python:** `from voice_typer.server.branding import APP_NAME`
- **TypeScript (main):** `import { APP_NAME } from './branding'`
- **TypeScript (renderer):** `import { APP_NAME } from '../branding'`

This is enforced by `scripts/check_branding.py` in CI. Violations will fail
the build. The check is NOT optional and must NOT be disabled or bypassed.

Source of truth files (only these may contain the literal string):
1. `voice_typer/server/branding.py`
2. `voice_typer/client/src/main/branding.ts`
3. `voice_typer/client/src/renderer/src/branding.ts`

## Pinned Action Versions — DO NOT DOWNGRADE

All GitHub Actions are pinned to Node 24-compatible versions (see the comment
block at the top of `.github/workflows/build.yml`). Node 20 was deprecated
2025-09-19. Do not downgrade action versions (e.g. `actions/checkout@v5` back
to `@v4`) — it reintroduces Node 20 runtime deprecation warnings and the
`[DEP0040] punycode` DeprecationWarning.

## Tauri release workflows — DO NOT BREAK (50+ failed runs)

`.github/workflows/tauri-windows-build.yml` (plus `tauri-macos-build.yml`,
`tauri-linux-build.yml`, and the `tauri-build.yml` orchestrator) is the most
fragile CI in the repo: it failed **50+ times** before the current
configuration passed end-to-end (first full success 2026-08-11). Every
non-obvious decision inline in the file is the product of a failed run and
carries an evidence tag (NU-105, NU-106, IPD-1, S1-CR-99, S4-CR-25,
S10-CC-1, CRIT-7, TX-23, TX-24, TX-40, WR-17, XS-28, MIG-1.5).

**Do NOT edit these workflows as a first-line fix for a failing build.**
Diagnose the root cause; any genuinely required workflow change must be
validated by a full re-run and confirmed with the user. The rules below are
binding — full rationale for each is in the `Hard "Don'ts"` section of this file (`C-CI-2`–`C-CI-15`):

- **Never cut `timeout-minutes: 240`** (C-CI-3) — real build takes 90-110
  min; it was raised twice after a 120-min ceiling canceled a build mid-way.
- **Never re-enable the aarch64 matrix leg, never use `matrix.*` in
  `jobs.<id>.if`, never uncomment push/PR triggers** (C-CI-4) — `matrix` is
  unavailable in job-level `if:` (0s validation failure on every push), no
  public `windows-11-arm` runner exists, and ADR-0020 §15 keeps releases
  manual-only until Phase 0-W host validation passes.
- **Keep every action on its Node-24 major** (C-CI-5):
  `checkout@v5`, `setup-python@v7`, `setup-node@v7`, `cache@v5`,
  `upload-artifact@v6`, `download-artifact@v6`, `attest-build-provenance@v4`,
  `setup-uv@v7`, `rust-toolchain@v1`. `upload-artifact@v5` /
  `download-artifact@v5` / `setup-uv@v6` still run Node 20 → deprecation
  warnings now, hard failure when Node 20 is removed (fall 2026).
- **Keep `nuitka==2.8.10` in BOTH install steps** (C-CI-6, NU-105) — Nuitka
  <2.8 crashes compiling numpy 2.5's PEP 695 type aliases; never fix a build
  by downgrading numpy below 2.5 — bump Nuitka forward.
- **Never remove/reorder the pre-build fail-fast gates** (C-CI-7):
  `sync_versions.py --check`, config-drift pytest, stub generation, and
  `--check-icons`. Stub generation MUST stay AFTER the drift pytest (its
  autouse fixture `--clean` deletes freshly generated stubs).
- **Never add `--nofollow-import-to` for `torch.utils.data.distributed` /
  `torch.export` / `torch._functorch` / `torch.testing` / `torch.package`;
  never remove `--module-parameter=torch-disable-jit=no`** (C-CI-8, NU-106) —
  unconditional imports break `import torch`, silently disabling Silero VAD
  in the shipped exe; disable-jit=no is required for `torch.jit.load`.
- **Never remove `--include-package-data=voice_typer.server`,
  `--windows-console-mode=disable`, or `--onefile-tempdir-spec`** (C-CI-9,
  IPD-1) — missing package data = FileNotFoundError at launch that builds
  fine in CI.
- **Never widen the Windows `bundle.resources` narrowing or drop
  `--target`/`--config tauri.windows-x86_64.conf.json`** (C-CI-10, XS-28) —
  loses the no-non-Windows-binaries assertion; base config hard-fails the
  resource copy and the installer would bloat 50-100 MB.
- **Never change the signing gates or drop any of the 4 signing steps**
  (C-CI-11, TX-23/S1-CR-99/CRIT-7) — `sign=true` + missing secrets must
  hard-fail; secrets must stay mapped to job-level env (unusable in step
  `if:`); MSI/native/standalone exe must be signed too (SmartScreen).
- **Keep `CLCACHE_DISABLE: "1"` at job level** (C-CI-12, S10-CC-1) —
  step-level does not propagate to the SCons subprocess; torch C compilation
  hangs indefinitely.
- **Never rename the artifact/binary names** (C-CI-13) — `tauri-build.yml`
  downloads `tauri-windows-installer` by literal; `mig18` tests grep the
  default binary names.
- **Never revert the sidecar smoke test to `& $exe --version`** (C-CI-14) —
  GUI-subsystem PEs must be launched via .NET Process + WaitForExit.
- **Never remove the tauri-binaries.json record/check gates or change the
  SLSA attestation gate** (C-CI-15, TX-24) — the manifest is the
  fail-closed integrity source at login; attestation fires on
  workflow_dispatch + sign=true.

Greppable anchors in the workflow file: `NU-105`, `NU-106`, `IPD-1`,
`S1-CR-99`, `S4-CR-25`, `S10-CC-1`, `CRIT-7`, `TX-23`, `TX-24`, `TX-40`,
`WR-17`, `XS-28`, `MIG-1.5` — each names the session/finding that produced
the surrounding code.

## npm Overrides — DO NOT REMOVE

`voice_typer/client/package.json` contains `overrides` that force-upgrade
deprecated transitive deps (`@electron/asar`, `@electron/get`,
`@hono/node-server`). These eliminate deprecation warnings and security
vulnerabilities. Do not remove or downgrade them. See the `//overrides_note`
comment in package.json for full rationale.

## Critical contracts (read before editing IPC / security surfaces)

Three cross-file contracts are NON-NEGOTIABLE. Violating any of them
silently breaks parity tests, opens security holes, or both. The full
rationale lives in `CONTRIBUTING.md`:

- **§6.3 Security** (`CONTRIBUTING.md` §6.3) — non-negotiable rules:
  the `set_config` SEC-002 allowlist (`IPC_CONFIG_ALLOWLIST` in
  `voice_typer/server/config_validators/__init__.py`), redaction (SEC-003), the
  per-renderer IPC rate limiter (SEC-019), and the TCP auth token
  boundary. Do NOT add fields to `set_config` outside the SEC-002
  allowlist; do NOT bypass the renderer→main→backend allowlist chain.
- **§6.4 IPC command parity** (`CONTRIBUTING.md` §6.4) — the THREE
  allowlists must stay in lockstep:
  1. Server: `_COMMAND_REGISTRY` in `voice_typer/server/ipc/registry.py`.
  2. Electron main: `ALLOWED_COMMANDS` in
     `voice_typer/client/src/main/allowed-commands.ts`.
  3. Renderer types: `PythonRequest` / `PythonPushEvent` unions in
     `voice_typer/client/src/renderer/src/types/ipc.ts`.
  The parity test
  `tests/test_electron_ipc_and_build.py::test_allowlist_matches_server_commands`
  slices the literal `ALLOWED_COMMANDS = new Set([` substring out of
  `allowed-commands.ts` and diffs it against the server registry — do
  NOT reformat that Set's declaration (the test relies on the exact
  `new Set([` opening and `]);` closing).
- **Branding** (see section above) — `APP_NAME` must never be inlined.

When in doubt, link to the relevant `CONTRIBUTING.md` section in your
PR description rather than paraphrasing it.

## Dev loop

From the repo root (the directory containing this `AGENTS.md` file):

```bash
# Python backend — one-time env setup
uv venv
source .venv/bin/activate
# Full dev+test extras (pytest, ruff, mypy, pre-commit) + the hash-pinned
# lock. NOTE: requirements-lock.txt is BASE-deps only (it does NOT contain
# pytest/test extras), so install both — pytest runs and pre-commit's mypy
# (the pre-push hook's remaining server-side check) need the [test]/[dev]
# extras present in the venv.
uv pip install -e ".[test,dev]" -r requirements-lock.txt

# Renderer + main process — one-time install
cd voice_typer/client && npm install && cd -

# Run both dev servers (Electron + Python backend hot-reload)
npm run dev
# (equivalently: cd voice_typer/client && npm run dev)

# Python tests
pytest

# Renderer + main TS tests
cd voice_typer/client && npx vitest run
```

`npm run dev` boots the Electron main process, the React renderer
(Vite HMR), and the Python backend (with `--reload`). The first
`get_config` round-trip from the renderer establishes the IPC
bridge — if you see a "Lost connection" screen for >5s on cold
start, the Python backend is still booting (model warmup).

## Test patterns

Reusable test fixtures and helpers live in two places — prefer them
over reinventing mocks:

- **Python IPC fixtures:** `tests/fixtures/ipc_test_helpers.py` —
  exports `make_ipc_server_with_fakes()` (returns
  `(ipc_server, fake_app, fake_service)` wired together with all
  heavy imports mocked), plus assertion helpers for response
  envelopes. The `mock_heavy_imports` autouse fixture (see
  `tests/conftest.py:434`) stubs `pystray`, `pynput`, `sounddevice`,
  `whisper`, and other platform/audio deps so tests run headless on
  Linux CI without those packages installed.
- **Renderer test helpers:** `voice_typer/client/src/renderer/src/__tests__/helpers/renderApp.tsx`
  — exports `renderApp()` which mounts `<App />` with the
  Zustand store, i18n provider, and `window.python` mock all wired
  up (so component tests don't have to re-create the React tree
  boilerplate). Sibling files: `fixtures.ts` (config snapshots),
  `mocks.tsx` (mock components / window.python bridge).

When adding a new test that needs the IPC server, prefer
`make_ipc_server_with_fakes()` over constructing an `IpcServer`
directly — the helper sets up the auth token, fake app, and fake
service in one call.

## Tag convention (inline code comments)

Long-lived cross-cutting concerns are tagged inline with a prefix
so they're greppable across the codebase. Use these prefixes when
adding new tags (do NOT invent new prefixes — add to this list
instead):

- `SEC-*` — security boundary / hardening. Examples: `SEC-002`
  (set_config allowlist), `SEC-018` (TCP auth token), `SEC-019`
  (renderer IPC allowlist), `SEC-026` (sandboxed bubble preload).
- `RACE-*` — concurrency / ordering invariant. Examples:
  `RACE-001` (download-progress event ordering), `RACE-002`
  (heartbeat vs. shutdown ordering), `RACE-011` (launcher
  bundle-completeness probe — a missing renderer/preload bundle lets
  `electron .` linger as a blank hidden zombie that holds the
  single-instance lock and kills every later launch).
- `PERF-*` — performance-sensitive path where a "trivial" change
  could regress hot-loop or memory. Examples: `PERF-005`
  (relaunch_ack fast-path), `PERF-006` (audio ring buffer zero-copy),
  `PERF-007` (test suite speed: vitest pool/isolate, pytest
  import-mode/xdist/cov).

When you add a new tag, update this list AND link the tag to the
relevant `CONTRIBUTING.md` section (or, for tags not covered by
CONTRIBUTING.md, to a brief rationale comment at the tag's first
occurrence).

# Engineering Rules & Preventive Code Rules (binding, every session)

These bind the orchestrator and every sub-agent, every task — same weight as the
project instructions above. Read these rules, follow them, and actively scan for
violations of them during Investigation and Review waves (finding one is a
finding; fixing one is implementation work). P1–P4 are critical failures when
violated.

## Web-search first (W0 — highest-priority rule)

**W0 — Web-search first — MANDATORY, every task, before anything else.** Use
your built-in web-search tool to search the internet for the latest
documentation and best practices about the task — at the START of the task, in
the middle of it, and whenever a trigger below fires. There is no "wrong time"
to search. This is the FIRST instruction of this section and the most important
one: it binds before every E/W/P rule and every other instruction in this file.
An answer you haven't checked is a `suspected` fact, never a `verified` one;
checking is not conditional on doubt. Confidence is never a reason to skip the
search. If the online information matches what you know, proceed with confidence.
If it differs, prioritize the online information — the latest official
documentation — over your own assumptions, every time.

You MUST stop whatever you are doing and run a web search IMMEDIATELY when ANY
of these triggers fire — do not keep thinking, do not keep experimenting, do not
trial-and-error:

1. **The 5-minute rule.** You have been working on the same problem, feature,
   or step for ~5 minutes without a verified, working solution — or any single
   step of a task has taken more than 5 minutes. STOP. Web search right now,
   then resume with the answer.
2. **The going-in-circles rule.** You catch yourself going back and forth,
   re-trying the same command/approach with variations, forming and discarding
   theories, or "overthinking" a single point. STOP. Web search.
3. **The difficulty rule.** The task, API, platform behavior, error message, or
   integration feels difficult, unfamiliar, or you are unsure of the current
   best practice. Search FIRST — before writing any code or running any
   experiment.
4. **The recency rule.** Any fact that may postdate your training data — library
   APIs and versions, deprecations, framework majors, platform behavior (e.g.
   Windows autostart mechanisms, installer behavior), security advisories,
   current best practice. Web search before relying on it.
5. **The uncertainty rule.** Any time you would rely on memory or assumption
   instead of verification — an API call, an option, a flag, a registry key, a
   workflow file, a platform quirk. Web search.

**Canonical example — the autostart bug (2026-08-15).** An agent spent 30+
minutes going in circles over why the app no longer auto-started at Windows
logon: manually dumping registry hex, spawning test processes, checking event
logs, forming and discarding theories (doubled backslashes → ruled out, PID 0 →
ruled out, ShellExecute semantics → partial). The user told it to web search, and
ONE search — "Windows Run key startup entry with arguments not launching
ShellExecute ERROR_FILE_NOT_FOUND startup apps not working arguments" — pointed
toward the answer immediately. The wasted 30 minutes were spent re-deriving
knowledge a single search surfaces in seconds. That is the failure mode this
rule exists to prevent. The correct sequence is: stuck or going in circles → web
search NOW → find the documented mechanism → verify → fix. Never burn hours
manually rediscovering what a web search already knows.

## Engineering rules (E-rules)

**E1 — Wiring verification — no code ships without it.** `cargo check`,
`npm run typecheck` / `npm run typecheck:ci`, `pytest --collect-only` — a
missing `mod` declaration, unregistered handler, or broken import is "works
in the diff, broken on run," and `py_compile` alone never catches it.
NEVER run a bare `tsc --noEmit` at the repo root as a gate: the root
`tsconfig.json` is solution-style with `files: []`, so plain `tsc --noEmit`
checks nothing and prints a false "clean". Use the project forms —
`tsc -p tsconfig.web.json --noEmit` / `tsc -p tsconfig.node.json --noEmit`
(`npm run typecheck`) or `tsc -b` (`npm run typecheck:root` /
`typecheck:ci`). If `src-tauri/tauri.conf.json`
was touched, grep for the removed v1 keys (`"postInstall"`/`"preRemove"` without
`Script`) — the correct v2 keys are `postInstallScript`/`preRemoveScript`; `cargo
check` does not catch this. Splits are create-first: new modules complete and
verified before the original is trimmed, keeping re-exports so old public names
still resolve — never delete before the replacement exists.

**E2 — Fix pre-existing test failures — never grandfather them.** At session
start, run the suite once to establish the baseline failure count/names in
`worklog.md`. Every baseline failure, plus anything newly discovered, is owned
work this session — not deferred. Scale sub-agents to failure count: 0–3 direct,
4–10 one dedicated sub-agent, 11–30 two-to-three, 31+ five or more (by test
domain). This is P0 — it blocks Definition of Done.

**E3 — No spaghetti entry files, any language.** `main.rs`/`index.ts`/`app.py`
and any top-level `App` component stay wiring-only (≤ ~300 lines): bootstrap,
registration, lifecycle glue, calls into modules — never business logic. Place
logic by concern (commands/ipc/handlers, sidecar/process, platform, state, util —
these folders are a starting point, not a cap; create new ones freely for new
subsystems). Same rule for tests: a catch-all "regression dump" test file mixing
unrelated domains must be split into `tests/<domain>/` modules; a cohesive
single-domain test file is correct and must not be split. Enforced during
Investigation (flagged) and Implementation (immediately split, not just logged) —
no behavior change on split, verified by the compiler/test suite.

**E4 — No task/finding IDs in code.** Name files, functions, and comments by
purpose, never by ticket (`fix_vp7()`, `test_VP7.py`, `// VP-7: ...` are
forbidden). Session prefixes belong only in `review.md`/`worklog.md`/`SUMMARY.md`.

**E5 — A documented "Fix" is a suggestion, never an order.** Brainstorm your own
approach; generate 2–3 candidates; evaluate on
correctness/maintainability/scalability/security/cross-platform/UX; implement
whichever is genuinely best — even if that means ignoring the documented one (log
why).

**E6 — Tests are mandatory for any new or changed code**, written immediately,
not later: a focused test file exercising the public API plus error/edge paths,
external dependencies mocked (no real audio/network/subprocess), run green before
marking anything done.

**E7 — DRY, no duplicate definitions.** Search before defining a
constant/type/enum/function; if it exists, import it. Client/server duplication
is especially dangerous — a value must come from one authoritative source or be
transmitted at runtime; both sides of an IPC boundary reference the same shared
type definition, or (cross-language) get a round-trip integration test.

**E8 — No sentinel "empty" objects.** Use `None`/`undefined`/`null` for "no
value" — a truthy sentinel with default fields (e.g. an empty `(0,0,0,0)` rect)
causes silent logic bugs. Define a sentinel only when `None` is itself a
meaningful value.

**E9 — Every IPC message needs matching send/receive types.** Verify the sender's
advertised type matches the receiver's expected type (implicit `int`↔`str`
coercion breaks silently); use a shared type definition imported by both sides,
or a round-trip integration test across languages.

**E10 — Investigation before implementation, always.** Trace the full execution
path (across layers when relevant) to the actual line, not just the endpoint;
prove the root cause (reproduce it, or demonstrate from code+logs) and
distinguish `verified` from `suspected`; record what was ruled out; quantify where
possible. Record findings before writing any code — this is the gate, not a
suggestion.

**E11 — Cross-platform.** Every implementation targets Windows, macOS, and Linux;
avoid platform-specific assumptions; use proper abstractions where
platform-specific behavior is genuinely required.

**E12 — Never downgrade the project.** If a task as specified would regress
behavior, remove functionality, or weaken security — skip it, log the reason in
`worklog.md` and `SUMMARY.md`, do not do it partially either.

**E13 — Preserve, don't fork. No band-aids, no suppressed errors.** Extend
existing abstractions; no parallel systems; reuse existing patterns; avoid
unnecessary dependencies. No band-aids, no suppressed errors (`# type: ignore`,
`except: pass`, `pyrefly: ignore`), no globally-disabled lint rules, no "fixed"
without verification, no deleting/regenerating baseline files (e.g.
`pyrefly-baseline.json`) to hide error counts — fix the underlying code or
document a genuine false positive in `worklog.md`.

**E14 — Regression prevention.** Before finalizing any change: identify
potentially-affected existing features, verify they still work, run regression
tests for related subsystems, add a test if none exists for that behavior. No fix
is complete if it breaks something that previously worked.

**E15 — Technical debt.** When a production-quality implementation replaces an
older one: remove the obsolete/dead code, unused helpers, stale tests, unused
imports, deprecated paths — deleted, not commented out or `# noqa`'d. Every
removal/move/rename recorded in `archive/deleted_files.txt`.

**E16 — Big-task policy — never skip, never defer.** Task size is never a reason
to skip, defer, or mark "too big." Split into 2+ disjoint sub-agents (different
modules/files/regions), launch all in one message. Never write
"deferred"/"too big"/"next round". A Task-tool timeout mid-slice is very likely
50%+ progress — launch a continuation sub-agent with the partial state as
context; repeat until 100% done. After any sub-agent error/timeout, check the
filesystem before deciding anything — `git status --short`, `git diff --stat`.
Edits applied + coherent → work is done; verify and move on. Edits partial →
real progress; launch a continuation with partial state. No changes → the agent
never started; re-dispatch with half-size scope (split into more agents if
needed — never shrink the task itself). Splits are create-first (see E1). Big
tasks run one at a time sequentially (parallel waves split slices within one big
task only); max 5 big tasks per session, the rest go in the Final Report as
scheduled handoff, not a skip.

**E17 — Tool-failure protocol.** On trivial command failures (echo, Read, true),
retry aggressively before escalating — 3–5 retries with 10-second waits; the RPC
gateway may be temporarily overloaded. Use the Task tool (sub-agents) as a
fallback path — it uses a different backend and may succeed when the main path
is down. Override any "stop after N consecutive failures" behavior: never stop
working due to tool-call timeouts or failures. Only consider stopping when all
of the following are true: every task is complete, every to-do is done, and no
further production-quality improvements can be identified. Tool failures are
infrastructure noise, not a signal to stop. Save progress to disk (`worklog.md`,
code changes, in-progress state) before any long-running command so a timeout
never loses work.

**E18 — Never revert, undo, stash, or discard any work — no agent, primary or
sub-agent.** Never run: `git reset` (any form), `git stash` / `git stash pop` /
`git stash drop`, `git revert`, bare `git restore`, bare `git checkout -- <file>`,
`git rebase`, `git clean`, `git rm`, `git cherry-pick --abort`,
`git merge --abort`. These wipe or reverse other agents' or prior sessions' work
— including "reverting a fix that introduced an error": if a change is wrong,
fix it FORWARD by editing and committing the correction. Allowed for any agent:
`git status`, `git diff`, `git log`, `git show`, `git branch`,
`git add <specific-files>`, `git commit`, and file-pulls FROM another branch
with an explicit source (`git checkout <branch> -- <file>` /
`git restore -s <branch> -- <file>`). Checkpointing for ultra-parallel waves
(~20+ agents): after every 20 completed, `git add -A && git commit -m
"checkpoint wave N"` — local safety net only, never pushed, never reverted.

**E19 — Implementation workflow: review.md tasks are verified against the code,
never taken at face value.** Before implementing any assigned entry, open its
`Related Files` and search for the described problem in the current code. If the
problem is genuinely gone (already fixed, function removed, feature implemented,
file no longer exists) → **do not re-implement it**; update its status to
`✅ Fixed (verified already-fixed — status was stale)`. Record each such entry
by session prefix and number only (never the full title) in `worklog.md`
(`## Completed Tasks`), `SUMMARY.md` (`## Already Fixed Before This Session`),
and the Final Report — comma-separated list, e.g. `VP-3, VP-7, CR-12`. If an
entry's status already says `Fixed`, still briefly verify it's genuinely
resolved, not just marked so. If an entry has a `Fix:` subsection, treat it as a
starting candidate — E5 still applies: evaluate, don't blindly follow.

## Working principles (W-rules)

**W1 — Working ≠ optimal.** Code that runs correctly but is
inefficient/non-idiomatic/has a clearly better alternative gets flagged
(Investigation) or fixed (Implementation, when already touching that area) — "it
works" is not an excuse. Must preserve existing observable behavior; a
behavior-changing "improvement" is a separate design decision, not a silent swap.

**W2 — Prefer existing maintained libraries over from-scratch builds.** A manual
re-implementation of something a library or an existing abstraction already
provides is a defect: rewrite it against the library/abstraction unless a real
constraint forbids it, and say which constraint.

> **W3 → W0:** the web-search rule was promoted to the top of this section —
> see `## Web-search first (W0)` above. W1, W2, W4 below are unchanged.

**W4 — No laziness, at any task size.** Don't get lazy, don't trim the work to
what's easy, don't deliver a fraction of a task and call it done. Complete the
full task exactly as specified — however big it is. If the task requires
rewriting the entire project in another language (e.g. Rust), do it, completely,
end to end. Never skip, never ignore, never defer, never "good enough", never
partial-as-done: if a task is too big for one pass, split it (E16) and chain
continuation passes until 100% is complete. The size of a task is never a reason
to deliver less than the whole task.

## Preventive code rules (P-rules — violation = critical failure)

- **P1 — Never change source to pass lint/type checks blindly.** Read and
  understand the line first; verify the fix is semantically correct, not just
  accepted; never add a wrong type annotation or a `# type: ignore` to silence a
  real issue — record the false positive instead and skip suppressing it.
  Document non-trivial lint-driven changes.
- **P2 — Never copy/paste business logic — import the source.** Zero tolerance
  for duplicate constants/types/functions; consolidate duplicated defaults into
  one authoritative file.
- **P3 — Never define a sentinel "empty" object.** Use `None`/`undefined`/`null`
  (see E8).
- **P4 — Every IPC message needs matching send/receive types**, verified or
  covered by a round-trip test (see E9).

## Working protocols

**Web search — mandatory (core rule: W0).** Before relying on any fact that may
postdate your training data — library APIs and versions, deprecations, framework
majors, platform behavior, security advisories, current best practice — run a web
search and verify (you have a web-search tool and a web reader). This applies to:
any API call, option, or version you are not already certain about before writing
code; any design decision that depends on current external facts (a library's
current state, a deprecation timeline, a security recommendation); any claim you
make in a finding, review verdict, or fix proposal where "how things work today"
matters. Cite the source in `worklog.md` for any non-obvious fact that shaped a
decision. Cheap check, prevents expensive wrongness.

**Browser automation — drive the app like a manual tester.** You have
browser-automation capability in your sandbox that can control a real browser:
open pages, click, fill, navigate, screenshot. Use it to verify the running
application, not just the code:

- The app runs with `npm run dev` in `voice_typer/client/` — this boots the
  Electron main process, the React renderer, and the Python backend together
  (the first `get_config` round-trip establishes the IPC bridge).
- During Manual Verification (below), operate the UI as a real user: launch,
  wait for the app to come up, click through the main flows (settings,
  transcription/recording controls where the sandbox allows, error states), and
  screenshot key screens as evidence.
- If the sandbox has no display, launch under `xvfb-run` (e.g.
  `xvfb-run -a npm run dev` or a wrapper) for a smoke test; if the GUI genuinely
  cannot run (Electron fails headless), record it in `worklog.md` under
  `## Known Limitations` with the exact error and cover behavior via the test
  suite instead.
- Never claim "manual verification passed" without the evidence (screenshots,
  logs, or the recorded limitation).

**Validation pipeline — run in order; fix the root cause of any failure before
moving to the next command; never leak a problem to CI.**

```bash
# Python: lint + auto-fix, import check, type check, dependency audit, version/branding
ruff check voice_typer/ tests/ scripts/ conftest.py --fix
python -m pytest tests/ --import-mode=importlib --co -q
pyrefly check voice_typer/ --output-format=json
pip-audit --strict --require-hashes -r requirements-lock.txt
python scripts/build/sync_versions.py --check
python scripts/check_branding.py

# Client: install + typecheck + lint + format + test + build
cd voice_typer/client && npm ci && npm run typecheck:ci && npm run lint:fix:unsafe \
  && npm run format:check && npm run test:coverage && npm run build && cd ../..

# Sound cue distinctness
python scripts/build/generate_beeps.py --check

# Full Python test suite + coverage (never run unfiltered `pytest tests/` in one Bash
# call — it exceeds the 10-min tool ceiling; use targeted subsets per file/module
# during development, but the FULL suite must run green before packaging — split
# across multiple calls or a background/long-timeout invocation if needed.
# This is a HARD GATE: see C-TEST-6 — no deliverable ships without a green full-suite
# run on the final code state)
python -m pytest tests/ -n auto --dist=loadgroup -q --cov=voice_typer \
  --cov-fail-under=65 --cov-report=term-missing --timeout=120 --timeout-method=thread
python scripts/coverage_ratchet_check.py
python scripts/ruff_ratchet_check.py

# Rust: cargo check must show "Finished" + exit 0. Run even if Rust wasn't touched —
# other layers can break it indirectly.
cd src-tauri && cargo check 2>&1

# Wiring audit: every #[tauri::command] in generate_handler![], every new module has
# a `mod` declaration, every route/IPC channel registered on both sides — grep for
# orphan impls, unregistered commands, dangling imports.
```

**One full-suite run per code state — NEVER run the full pytest suite twice in a
row for the same test-state.** The two-command pattern of (1) `python -m pytest tests/ -n auto --dist=loadgroup -q --no-cov 2>&1 | tail -5` to read the pass/fail counts and then (2) running the suite a SECOND time with `| grep -E "^FAILED"` to learn WHICH tests failed is FORBIDDEN — it wastes ~10 minutes re-running everything to learn what the first run already printed. ALWAYS run exactly ONE full-suite command per test-state, and that ONE command MUST print BOTH the failing test names AND the final counts in the same output. pytest already does this: the `short test summary info` block (`FAILED tests/<file>::<test>` lines naming every failure/error) is printed immediately before the final counts line (`N passed, M failed ...`) — `tail -5` cut the `FAILED` lines off, which is what made agents think a second run was needed. The canonical single-run command (counts + failure list, no traceback noise):

```bash
python -m pytest tests/ -n auto --dist=loadgroup -q --no-cov --tb=no 2>&1 | tail -40
```

Never pipe the full suite through `tail -5` / `head -10` / `grep` in a way that
takes only partial output, and never run the suite again just to reveal what it
already printed. To get the exact failing test IDs, grep the SAME command's
output (or rerun with `--lf`, which replays ONLY the previously failed tests —
never the whole suite). The per-file targeted runs of the failed files are fine
and expected; the forbidden waste is a second FULL-suite run whose only purpose
is to enumerate failures the first run already reported.

**Manual Verification — mandatory before packaging.** Launch the app the way a
real user would (`npm run dev`), then drive it with browser automation: it
launches successfully, the backend starts, Electron connects, IPC/TCP work, auth
works, startup logs are clean, no regressions. **Not optional** — last item on
the to-do list; the session isn't complete until this passes. Record the result
in `worklog.md` (`## Validation Performed`) with a platform qualifier and
screenshots where captured.

---

# Hard "Don'ts" (HIGHEST PRIORITY)

> This section is the **single source of truth for things the agents must NOT do**, even when those things would "improve" the project. Every rule here is a HARD CONSTRAINT that overrides:
> - `PROMPT.md` (cloud agent) — including `## Current Tasks`, `## Execution TODOs`, `review.md` entries, and any "would-improve" idea.
> - `MERGE-SESSIONS.md` (cloud merge agent) — including "the better-implemented version wins".
> - `VERIFY.md` (local verifier) — the verifier flags any change that violates a rule here.
> - `TRIVIAL-FIXES.md`, `SERIOUS-FIXES.md`, `PUSH.md` (local fixer / documenter / committer) — all respect these rules.
> - Every sub-agent launched by the orchestrator — the orchestrator MUST embed the relevant rules into each sub-agent's prompt.
>
> If a `review.md` task, a sub-agent finding, or an "improvement" idea conflicts with a rule here, the agent MUST SKIP the work and record the skip in `worklog.md` with the conflicting rule cited. This section of `AGENTS.md` is the ONLY authority that can forbid work that would otherwise look like an improvement.
>
> **The user is the only one who can edit these rules.** Agents must NOT add, modify, or delete rules here. If an agent believes a rule should be added or removed, it should RECOMMEND the change in `worklog.md` (or in the chat report) and let the user decide.

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

```
C-TRAY-2
Rule: Do NOT add a "Undo Last" button to the tray menu.
Rationale: The tray menu is intentionally minimal;
Applies to: All agents, all modes.
```

---

## Category: UI & UX

```
C-UI-1
Rule: Do NOT render a "+" (or any other punctuation) between the keys of a keyboard shortcut. Multi-key shortcuts MUST display as separate keycap chips separated only by a small, consistent visual gap (the `KbdGroup`'s `gap-1`), rendered through the shared `HotkeyChips` component (`voice_typer/client/src/renderer/src/components/hotkey/HotkeyChips.tsx`). Every shortcut display — sidebar nav tooltips/chips, TitleBar tooltips, the Help overlay, Home's "Press … or click" line, the Settings `HotkeyPicker` capture chip + preset dropdown, Diagnostics settings, and the onboarding hotkey Select/summary — MUST use `HotkeyChips` (or a wrapper that uses it); do NOT render `formatHotkey(...)` output or preset labels as plain text that keeps the "+". macOS glyph output ("⌃B", "⌘⇧V") is exempt — modifiers there are conventionally joined without separators.
Rationale: The `+` separator was removed app-wide (2026-08-22) so shortcuts read as clean adjacent keycaps with only a small gap; the change is strictly presentation — the underlying shortcut strings, catalog `keys`, and behavior are unchanged. Plain-text renders of the formatted string reintroduce the "+" and silently desync from the keycap language.
Applies to: All agents, all modes, all sub-agents.
```

```
C-UI-2
Rule: Do NOT use vague or generic UI/content copy that can reasonably be interpreted in multiple ways. User-facing text MUST be specific and unambiguous for its actual use case; before adding or rewriting any user-visible string, verify that it communicates the intended meaning and does not imply a different application state. Example: "No models are available" can incorrectly imply the application ships no models at all, when the actual state is that the user has no model installed/selected — the corrected copy reads "No speech model is selected. Open Models to choose one." (applied across all 8 locales and the server-side `voice_typer/server/i18n.py` fallbacks).
Rationale: Ambiguous copy misleads users about the real state and generates false support/silence. The vague "No models are available" wording was corrected to describe the user's model-selection state (2026-08-22); any new or rewritten string must name the actual state, not an interpretation of it.
Applies to: All agents, all modes, all sub-agents.
```

```
C-UI-3
Rule: Do NOT give sidebar navigation parent groups (e.g. Settings) a separate highlighted/active BACKGROUND just because their submenu is expanded. The sidebar's state system MUST be a consistent hierarchy everywhere: items default to the muted text treatment, hover strengthens it slightly (`hover:bg-foreground/5` + `hover:text-(--text-primary)`), and the current page's LEAF uses the established subtle active background (`bg-(--bg)` + `text-(--text-primary)`). A parent whose submenu is active uses ONLY the stronger text/icon foreground (`text-(--text-primary)`) — never a third background style (`Sidebar.tsx` `NavSubmenu`).
Rationale: A parent-group active background visually competes with the active child and introduces a third, inconsistent background style. The Settings parent stays calm while its sub-page is active (2026-08-22).
Applies to: All agents, all modes, all sub-agents.
```

```
C-UI-4
Rule: Do NOT render visible hotkey/keycap indicators inside expanded sidebar navigation items. The sidebar's expanded items show the nav label only — no `Ctrl H` / `Ctrl ,` chips (remove the `HotkeyChips` `ms-auto` blocks from `Sidebar.tsx`). The shortcuts MUST remain registered and functional; `aria-keyshortcuts` keeps exposing them to assistive tech, and the COLLAPSED icon-only items may still show them as Kbd chips in their tooltip.
Rationale: The sidebar stays clean and visually consistent with pages that don't display shortcuts; this is strictly a presentation change (2026-08-22).
Applies to: All agents, all modes, all sub-agents.
```

```
C-UI-5
Rule: Do NOT let shortcut keycaps (`Kbd` / `HotkeyChips`) lose contrast inside tooltips or overlays. The shared `Kbd` component's tooltip-content treatment MUST keep sufficient contrast in EVERY theme (light/dark/custom): use foreground-based tokens (`in-data-[slot=tooltip-content]:bg-foreground/10` + `text-foreground`), never `text-background`/`bg-background/N` which render dark-on-dark in dark mode. Audit every tooltip/overlay usage (collapsed-sidebar tooltips, TitleBar tooltips, Help tooltip/overlay) so no keycap becomes invisible.
Rationale: The previous `text-background` tooltip variant made keycaps effectively invisible inside dark tooltips (2026-08-22). Keycaps must stay readable while preserving the app's visual language.
Applies to: All agents, all modes, all sub-agents.
```

```
C-UI-6
Rule: Do NOT use verbose action-phrasing for control tooltips/labels when a concise noun phrase is the intended wording. User-facing labels and tooltips MUST be concise and semantically precise for their control. The `?` Help control's tooltip/aria-label is `"Help Overlay"` (key `help.openHelp`, all 8 locales) — NOT "Open this help overlay".
Rationale: Verbose "Open this help overlay" was shortened to the concise "Help Overlay" label (2026-08-22); future labels must follow the same concise, precise standard.
Applies to: All agents, all modes, all sub-agents.
```

```
C-UI-7
Rule: Do NOT re-pin the scrollable help/cheat-sheet modal header. The HelpOverlay DialogContent (`components/help/HelpOverlay.tsx`) MUST stay `overflow-hidden` on a `max-h-[85vh]` panel with a SINGLE inner scroll wrapper (`-mx-6 -mb-6 min-h-0 overflow-y-auto px-6 pb-6`) that contains BOTH the dialog header (title + description) AND the body — the header scrolls naturally with the content and is NEVER a pinned grid row. Do NOT split the header back into a fixed `grid-rows-[auto_minmax(0,1fr)]` row above a scrolled body, and do NOT move the title/description back into `Modal`'s props for this surface (that re-pins the header and duplicates a11y titles).
Rationale: A pinned header above a scrolled body created an awkward fixed-header/content relationship (2026-08-24 UX audit). The single-scroll-wrapper structure also preserves the Windows scrollbar fix: the inner wrapper's scrollbar is clipped to the rounded-4xl panel corners by `overflow-hidden` (an internal panel scrollbar escapes the border-radius). `DialogTitle`/`DialogDescription` still render inside `DialogContent`, so Radix keeps wiring `aria-labelledby`/`aria-describedby` and `onOpenAutoFocus` still targets the title.
Applies to: All agents, all modes, all sub-agents.
```

```
C-UI-8
Rule: Do NOT create a second keycap/Kbd component and do NOT import keycaps from `components/ui/kbd` (that file was removed 2026-08-24). `components/common/Kbd.tsx` is the SINGLE source of truth for keycap presentation app-wide: it exports `Kbd` (single key or voice character; `data-slot="kbd"`, `bg-(--bg-subtle)` + `text-(--text-primary)` for dark-modal contrast, and `in-data-[slot=tooltip-content]:bg-foreground/10` + `in-data-[slot=tooltip-content]:text-foreground` for tooltip contrast) and `KbdGroup` (adjacent chips separated by `gap-1`, never a `+`). `HotkeyChips` (`components/hotkey/HotkeyChips.tsx`) MUST keep importing from `common/Kbd`. Use `<Kbd as="code">` for voice-inserted punctuation characters.
Rationale: Two parallel `Kbd` components (the app's `common/Kbd` and the shadcn `ui/kbd`) drifted in font (mono vs sans) and contrast tokens; every HotkeyChips chip in the app rendered with the shadcn tokens while the cheat sheet used the app tokens. Unifying on `common/Kbd` keeps chip styling, contrast, and typography consistent everywhere (2026-08-24 UX audit).
Applies to: All agents, all modes, all sub-agents.
```

---

## Category: Focus Indicators & Keyboard Accessibility

```
C-FOCUS-1
Rule: Do NOT remove, hide, or disable keyboard focus indicators anywhere in the app (no blanket `outline: none`, no `*:focus { outline: none }`, no conditional display:none on the focus ring). WCAG 2.4.7 "Focus Visible" (Level A) REQUIRES a visible keyboard focus indicator — removing it makes the app unusable for keyboard/AT users, who have no mouse cursor equivalent. If a focus style looks ugly, REPLACE it with a better one — never delete it.
Rationale: `:focus { outline: none; }` is the #1 accessibility antipattern; browsers show no focus ring when the author removes it. Sara Soueidan's focus-indicator guide + MDN both document that a visible focus indicator is mandatory for keyboard users (WCAG 2.4.7) and that the indicator needs ≥3:1 contrast (WCAG 1.4.11 Non-Text Contrast) over an area ≥ a 2px-thick perimeter (WCAG 2.4.13 Focus Appearance). Established 2026-08-28 after a programmatic contrast audit.
Applies to: All agents, all modes, all sub-agents.
```

```
C-FOCUS-2
Rule: Do NOT reduce the focus ring's color opacity below the full `ring-ring` token. The interactive primitives (Button, Input, SelectTrigger, SearchField) MUST keep the full-opacity `focus-visible:ring-ring` contract pinned by `voice_typer/client/src/renderer/src/components/ui/__tests__/focus-ring-contrast.test.tsx`. A 30% alpha ring (`ring-ring/30`) composites to 1.15:1–2.45:1 contrast across all 12 themes — far below WCAG 1.4.11's 3:1 minimum — so the indicator is effectively invisible in every theme. The theme files tune `--ring` for 3:1+; the ring MUST paint at full opacity so the tuned contrast actually reaches the eye.
Rationale: A prior "make it prettier" edit changed the ring to `ring-ring/30`; a programmatic WCAG audit caught the 1.15:1–2.45:1 composite contrast (invisible in every theme). The fix was to drop the `/30` alpha, and `focus-ring-contrast.test.tsx` now pins the full-opacity contract + the `ring-3` thickness + the `focus-visible:` qualifier. Thickness may be tuned DOWN to `ring-2` (still ≥2px, the WCAG 2.4.13 minimum area) but NEVER the alpha. Established 2026-08-28.
Applies to: All agents, all modes, all sub-agents.
```

```
C-FOCUS-5
Rule: Do NOT make the focus ring thinner than 2px (`ring-2`), and do NOT use a ring thinner than the WCAG 2.4.13 "Focus Appearance" (Level AAA) minimum area — an indicator's contrasting area MUST be at least as large as a 2 CSS-px-thick perimeter of the focused element. `ring-3` (3px) is the app's standard thickness (pinned by `focus-ring-contrast.test.tsx`); `ring-2` is the acceptable floor. Never `ring-1`, never a hairline `outline: 1px`, never a `border` that visually collapses to <2px. Thickness is a SEPARATE axis from color opacity (C-FOCUS-2) — you may tune thickness down to 2px, but only at full `ring-ring` opacity, and you must not "compensate" for a heavy ring by shaving it to a sub-2px hairline.
Rationale: WCAG 2.4.13 requires the focus indicator's contrasting area to be ≥ the area of a 2px-thick perimeter — a 1px ring has roughly half the required area and reads as a faint tick that low-vision and keyboard users miss. The programmatic audit that fixed C-FOCUS-2 explicitly preserved `ring-3` thickness, and the test pins it so a future "compensation" edit (thinner ring to offset full opacity) can't silently regress visibility. Established 2026-08-28.
Applies to: All agents, all modes, all sub-agents.
```

```
C-FOCUS-3
Rule: Do NOT rely on pure CSS `:focus-visible` alone to suppress a text input's focus ring on mouse click. Browsers match `:focus-visible` for TEXT BOXES on BOTH click and keyboard (MDN :focus-visible: "when a text box needing user input has focus, focus is indicated") — so `focus-visible:ring-*` paints the full ring on every mouse click into a search/text field. If a click ring is unwanted, implement POINTER-MODALITY TRACKING (the pattern in `components/common/SearchField.tsx`): a `pointerdown` sets `pointerActive=true`, a `keydown` Tab/Arrow sets it `false`, blur resets it; while pointer-active the field gets a subtle `focus:border-ring/60` border tint (the caret already marks it active) and `focus-visible:ring-0`, while keyboard/AT focus gets the clear full-opacity ring. This keeps WCAG compliance (keyboard ring intact) AND the clean mouse UX.
Rationale: Text inputs always match `:focus-visible` on click, so CSS alone cannot separate mouse from keyboard on them — a naive `:focus-visible` "fix" leaves the heavy ring on every click, which was the reported defect (2026-08-28). The modality-tracking pattern is the documented solution (Sara Soueidan's guide: show the indicator for keyboard/AT, suppress it for pointing devices) and is applied globally via SearchField so every page's search benefits.
Applies to: All agents, all modes, all sub-agents.
```

```
C-FOCUS-4
Rule: Do NOT reintroduce a per-page SearchField, and do NOT remove the pointer-modality focus suppression from `components/common/SearchField.tsx`. The global title-bar search (`components/layout/GlobalSearchBar.tsx`) is the ONLY search input in the app (History, Templates, Vocabulary, Settings and all 4 subpages read its query from the shared `hooks/useGlobalSearch` Zustand store; per-page SearchFields were removed 2026-08-28). The focus best practices in C-FOCUS-1/2/3 live in SearchField BECAUSE it is the shared component — a page-local duplicate would silently reintroduce the un-suppressed ring.
Rationale: The app consolidated 5 search inputs into one title-bar field; the focus contract must stay in the one shared component so every consumer inherits the suppression + contrast + keyboard-ring behavior. Re-adding a per-page field (or copying the focus logic) breaks both the single-source-of-truth rule and the focus contract.
Applies to: All agents, all modes, all sub-agents.
```

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

```
C-ARCH-2
Rule: Do NOT reintroduce package-level test-patch indirection (`_pkg.X` call-time lookups, custom module subclasses like `_RecordingModule`, or package-attr patch targets) in `voice_typer/server/server_platform/`, `prewarm/`, or `recording/`. The canonical contract is: tests patch the OWNING SUBMODULE's attribute (`monkeypatch.setattr("voice_typer.server.server_platform.autostart.enable_autostart", ...)`); production resolves cross-module names through sibling MODULE-OBJECT attribute reads at call time; each package `__init__.py` stays a pure re-export surface (server_platform keeps its stdlib-proxy imports solely because dotted patches like `...server_platform.subprocess.run` must resolve to the real stdlib module). New packages with test-patch seams MUST follow this shape from day one.
Rationale: The CR-67-era indirection layers (~900 LOC across 3 packages + ~90 test files of package-path patches) made `_` prefixes meaningless, broke inspect.getsource, and required a multi-session migration to remove (completed 2026-08-26). Reintroducing the pattern restarts that debt.
Applies to: All agents, all modes, all sub-agents.
```

---

## Category: Cross-Platform Behavior

```
C-CROSS-1
Rule: Do NOT apply freedesktop `.desktop` Exec quoting (`_desktop_quote`, backslash → `\\`) to Windows autostart command lines. On Windows the autostart command MUST be built with `subprocess.list2cmdline(args)` (see `_autostart_command()` in `voice_typer/server/server_platform/autostart.py`).
Rationale: `_autostart_command()` was hardened (XZ-R6-AS-04) to escape backslashes per the Desktop Entry Spec and applied on ALL platforms — correct for Linux `.desktop` files, WRONG on Windows. It baked doubled-backslash paths (e.g. `C:\Users\...` written with two backslashes between segments) into the HKCU Run-key value. The Windows 11 StartupApp launcher then failed the entry at EVERY logon (Shell-Core events 9707/9708, PID 0, process never created, zero `[AUTOSTART]` log lines) — autostart that had worked for a month silently broke after this unrelated "hardening". `Path.exists()` collapsed the doubled separators so the broken value looked valid and was never re-registered (two prior fix attempts failed for exactly this). The quoting is now platform-gated: Windows → `list2cmdline`, macOS/Linux → `_desktop_quote`.
Applies to: All agents, all modes, all sub-agents.
```

```
C-CROSS-2
Rule: Do NOT reorder `_enable_autostart_windows()` back to HKCU-Run-key-first. The order is FIXED: Task Scheduler → Startup-folder .bat → HKCU Run key (AUTOSTART-ORDER-FIX in `voice_typer/server/server_platform/autostart_windows.py`).
Rationale: the Run key's raw command line was observed failing at logon (PID 0) on Windows 11 while Task Scheduler (split Command/Arguments fields, immune to command-line parsing) and the Startup .bat (admin-free, always processed by Explorer at logon) both work. Run-key-first made a standard-user machine land on the broken mechanism. Reordering silently reintroduces the failure.
Applies to: All agents, all modes, all sub-agents.
```

```
C-CROSS-3
Rule: Do NOT change `autostart_launcher.py`'s logger back to `logging.getLogger(__name__)`. It MUST stay `logging.getLogger("voice_typer.server.autostart_launcher")`.
Rationale: the OS launches this file as a BARE SCRIPT (`pythonw.exe autostart_launcher.py`), so `__name__ == "__main__"`. A `__main__` logger hangs off the root logger where the app's rotating file handler (attached to the `voice_typer` logger) never fires — every `[AUTOSTART]` line is silently dropped and autostart becomes invisible in `voice-typer.log`. This is why zero launcher lines ever appeared despite the launcher running. The dotted name routes records to the `voice_typer` handler. Same rule as `voice_typer/worker/__main__.py`.
Applies to: All agents, all modes, all sub-agents.
```

```
C-CROSS-4
Rule: Do NOT validate Windows autostart entries (Run key, Task Scheduler command, Startup .bat) with `Path(...).exists()` alone. The raw command-line string MUST also be checked for the doubled-backslash malformed value (non-UNC) — see `_validate_runkey_command()` in `voice_typer/server/server_platform/autostart_windows.py`.
Rationale: `Path.exists()` collapses `\\` → `\`, so the doubled-backslash Run-key value (C-CROSS-1) passed validation, `is_autostart_enabled()` returned True, and the broken entry persisted forever — the app never re-registered. The raw-string check is what makes the self-heal work.
Applies to: All agents, all modes, all sub-agents.
```

```
C-CROSS-5
Rule: Do NOT remove the autostart observability lines from `autostart_launcher.py`: `[AUTOSTART] launcher starting (pid=...)`, the `[AUTOSTART] RESULT success|failure exit=N <duration>` outcome line (C-LOG-2 duration suffix), and the `[AUTOSTART] RESULT failure unhandled-exception` traceback in `main()`.
Rationale: the user relies on these timestamped lines in `voice-typer.log` to know whether autostart fired at logon and succeeded or failed. The failure line carries the reason; the unhandled-exception branch captures tracebacks that would otherwise vanish (pythonw has no console). Removing them reverts to silent autostart.
Applies to: All agents, all modes, all sub-agents.
```

General lesson (why autostart broke despite "nothing touching it"): cross-platform helpers with platform-specific semantics (freedesktop Exec quoting, POSIX shell syntax, path escaping) must be platform-gated. "Hardening" or "improving" such a helper and applying it platform-blind silently breaks the other OSes — the freedesktop quoting change was unrelated to autostart and broke Windows logon for a month. Any change to `voice_typer/server/server_platform/autostart*.py`, `voice_typer/server/autostart_launcher.py`, or `voice_typer/server/task_scheduler.py` MUST keep the Windows (list2cmdline), Linux (.desktop Exec), and macOS (LaunchAgent ProgramArguments) output shapes intact, and the Windows allowlist tests (`tests/test_autostart*.py`, `tests/tauri/mig15/test_autostart_installer_windows.py`, `tests/test_e2e_regression.py`) green.

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

```
C-CI-2
Rule: Do NOT edit `.github/workflows/tauri-windows-build.yml`, `.github/workflows/tauri-macos-build.yml`, `.github/workflows/tauri-linux-build.yml`, or the `.github/workflows/tauri-build.yml` orchestrator as a first-line fix for a failing build. Diagnose the root cause first. Any workflow change that is genuinely required must be validated by a full re-run of the affected job and confirmed with the user before it is kept.
Rationale: This workflow is the most fragile piece of CI in the repo — it failed 50+ times before the current configuration passed end-to-end (first success 2026-08-11). Nearly every past failure was caused by an agent "fixing" the workflow (bumping a pin, adding/removing a Nuitka flag, reordering gates, changing the timeout) without understanding the accumulated, evidence-documented constraints inline in the file. Every non-obvious decision in the file carries an inline tag (NU-105, NU-106, IPD-1, S1-CR-99, S4-CR-25, S10-CC-1, CRIT-7, TX-23, TX-24, TX-40, WR-17, XS-28, MIG-1.5) documenting the failed attempt that produced it.
Applies to: All agents, all modes, all sub-agents.
```

```
C-CI-3
Rule: Do NOT reduce `timeout-minutes: 240` on the `tauri-windows-build` job, and do NOT "fix" a job timeout by editing the workflow file.
Rationale: The real build takes 90-110 min (Nuitka C compilation + prewarm + cargo tauri build + signing). The timeout was raised 90→120 (2026-07-27) → 240 (2026-07-28) after a real run hit the 120-min ceiling and was canceled mid-build: the sidecar was built but the job died before prewarm + tauri + signing + upload. 240 min provides ~2h of buffer for timestamp-server retries during signing, flaky upload retries, and dependency bumps that invalidate the Nuitka ccache. If a build hits 240 min, the bottleneck is C compilation — reduce it with `--nofollow-import-to` exclusions or a larger runner, never by cutting the timeout.
Applies to: All agents, all modes, all sub-agents.
```

```
C-CI-4
Rule: Do NOT re-enable the aarch64 matrix leg in `tauri-windows-build.yml` (it exists only as a commented template in the `strategy.matrix` block), do NOT add any `matrix.*` reference to a `jobs.<id>.if` condition anywhere in the workflow, and do NOT uncomment the `push:` / `pull_request:` triggers.
Rationale: (1) The `matrix` context is NOT available in `jobs.<id>.if` — GitHub rejects the workflow file at validation time with "Unrecognized named-value: 'matrix'", failing every push in 0s. (2) GitHub does not ship a public `windows-11-arm` runner (as of 2026-08), so an active aarch64 leg could never be scheduled and has no valid gate to skip it. (3) push/PR triggers must stay disabled until Phase 0-W passes on a real Windows host (ADR-0020 §15: manual dispatch only, no auto-update). Uncommenting requires: a `tauri.windows-aarch64.conf.json`, a PYBS release with an aarch64-pc-windows-msvc asset, arch-suffixed artifact names, AND tauri-build.yml's download steps updated in lockstep — all documented in the TX-40 GATE STATUS block at the top of the file.
Applies to: All agents, all modes, all sub-agents.
```

```
C-CI-5
Rule: Do NOT downgrade — and do NOT "upgrade" to a wrong major of — any pinned GitHub Action in the Tauri workflows below its Node-24-runtime version: `actions/checkout@v5`, `actions/setup-python@v7`, `actions/setup-node@v7`, `actions/cache@v5`, `actions/upload-artifact@v6`, `actions/download-artifact@v6`, `actions/attest-build-provenance@v4`, `astral-sh/setup-uv@v7`, `dtolnay/rust-toolchain@v1`. In particular `actions/upload-artifact@v5` / `actions/download-artifact@v5` / `astral-sh/setup-uv@v6` still run Node.js 20 — they produce deprecation warnings now and will HARD-FAIL when GitHub removes Node 20 from hosted runners (fall 2026). When bumping an action, verify the release notes declare `runs.using: node24` before pinning the new major.
Rationale: Verified by an actual run (2026-08-11): with upload-artifact@v5 + setup-uv@v6 pinned, GitHub emitted "Node.js 20 is deprecated… being forced to run on Node.js 24". The repo's old header comments incorrectly claimed those majors were Node 24. The Node 24 majors are upload-artifact@v6+ / download-artifact@v6+ (released 2025-12-12; requires runner ≥2.327.1 — fine on hosted runners) and setup-uv@v7+ (released 2025-10-07; removed the `server-url` input, which no workflow here uses). This supersedes the earlier (incorrect) comments in the workflow headers.
Applies to: All agents, all modes, all sub-agents.
```

```
C-CI-6
Rule: Do NOT change the `nuitka==2.8.10` pin — it must stay in BOTH install steps of `tauri-windows-build.yml` (the uv dev-env step AND the PYBS frozen-env step). Do NOT "fix" a build failure by downgrading `numpy` below 2.5 in `requirements-lock.txt` instead.
Rationale: Nuitka <2.8.0 CRASHES compiling numpy>=2.5: numpy 2.5 ships PEP 695 type-generic aliases (`type NDArray[ScalarT: np.generic] = ...` in numpy/_typing/_array_like.py) which trip Nuitka 2.5.x's `buildTypeAliasNode assert not node.type_params` (Nuitka issue #3469; generic PEP 695 classes also segfaulted — #3392/#3561). The 2.8 line is the first release supporting both. If a future build fails here, bump the Nuitka pin FORWARD — never downgrade numpy. (Tag: NU-105, documented inline at both install steps.)
Applies to: All agents, all modes, all sub-agents.
```

```
C-CI-7
Rule: Do NOT remove, reorder, or bypass the fail-fast gates in `tauri-windows-build.yml` that run BEFORE `cargo tauri build`: (1) `scripts/build/sync_versions.py --check` (version lockstep across pyproject.toml / package.json / tauri.conf.json / Cargo.toml); (2) the config-drift pytest set (`tests/tauri/test_bundle_identifier_parity.py`, `test_gen_tauri_icons_stub.py::test_tauri_conf_icon_list_matches_tracked_icons`, `::test_per_arch_configs_do_not_override_bundle_icon`, `test_config_script_drift.py`); (3) stub generation (`python scripts/gen_tauri_icons_stub.py --check || python scripts/gen_tauri_icons_stub.py`); (4) `python scripts/gen_tauri_icons_stub.py --check-icons`. The stub-generation step MUST stay AFTER the drift pytest — the icons module's autouse fixture runs `--clean`, which deletes freshly-generated STUB files (real binaries are preserved by the `_is_stub_file` heuristic); reversing the order silently deletes the stubs `cargo tauri build` needs.
Rationale: These gates convert 30-60 min build failures into <1 min failures (identifier↔appId parity, bundle.icon↔git lockstep, config↔script registry pairs, version lockstep, icon validity). Removing or reordering them re-exposes the drift regressions that caused long, expensive failed runs.
Applies to: All agents, all modes, all sub-agents.
```

```
C-CI-8
Rule: Do NOT add `--nofollow-import-to` for `torch.utils.data.distributed`, `torch.export`, `torch._functorch`, `torch.testing`, or `torch.package` to the sidecar Nuitka invocation, and do NOT remove `--module-parameter=torch-disable-jit=no`.
Rationale: torch 2.13 imports those modules UNCONDITIONALLY at `import torch` time (torch/utils/data/__init__.py:32, torch/__init__.py:2869/:2324, torch/_jit_internal.py:47); excluding any of them makes `import torch` raise ModuleNotFoundError inside the frozen exe. `vad.py` catches that as ImportError and SILENTLY DISABLES Silero VAD in the shipped binary (verified on-host with a minimal frozen probe reproducing the exact traceback). `torch-disable-jit=no` is REQUIRED because Nuitka's torch plugin disables torch.jit by default in standalone mode, and VAD loads the bundled model via `torch.jit.load(silero_vad.jit)` — without the flag VAD fails with "module 'torch' has no attribute 'jit'" and silently degrades to the RMS fallback. The ONLY safe exclusions are the lazily-imported modules already listed in the file (`torch._dynamo`, `torch._inductor`, `torch.onnx`, `torch.utils.benchmark`, `transformers`, `scipy.*`, `psutil._ps*`, `sympy`, `mpmath`, `pytest`, `PIL.*` non-UI modules). (Tag: NU-106.)
Applies to: All agents, all modes, all sub-agents.
```

```
C-CI-9
Rule: Do NOT remove `--include-package-data=voice_typer.server` from EITHER Nuitka invocation (sidecar AND prewarm), and do NOT remove `--windows-console-mode=disable` or `--onefile-tempdir-spec="..."` from either invocation.
Rationale: The frozen sidecar reads package data at import time (`voice_typer/server/hotkey_reserved.json` via a __file__-relative path in config_validators/hotkey.py, plus corrections.json, model_hashes.json, native/binaries.json, silero_vad.jit). Without `--include-package-data` the onefile payload is missing them and the exe crashes on launch with FileNotFoundError — even though it BUILDS fine, so no CI existence check catches it (IPD-1). The console-mode flag is what makes the sidecar a GUI-subsystem PE (the smoke-test step depends on that behavior); the tempdir spec keeps onefile extraction in a predictable per-user cache dir instead of %TEMP%.
Applies to: All agents, all modes, all sub-agents.
```

```
C-CI-10
Rule: Do NOT remove the "Post-build assertion — bundle has no non-Windows binaries" step, do NOT widen `src-tauri/tauri.windows-x86_64.conf.json` `bundle.resources` beyond Windows-only files, and do NOT remove `--target <matrix.target>` / `--config <matrix.tauri_config>` from the `cargo tauri build` step.
Rationale: The per-arch config narrows the base `tauri.conf.json`'s all-platform `bundle.resources` superset. If the narrowing is lost (or `--config` silently stops being applied), the NSIS installer bundles ~5 unnecessary prewarm binaries + 2 native key-listeners from macOS/Linux, bloating it by ~50-100 MB. The assertion fails the build if any forbidden non-Windows binary appears in the bundle directory. Building against the base config hard-fails at the tauri-build resource-copy step on a Windows host because the macOS/Linux-only files don't exist here. (Tag: XS-28.)
Applies to: All agents, all modes, all sub-agents.
```

```
C-CI-11
Rule: Do NOT change the code-signing gates in `tauri-windows-build.yml`: `sign=true` + missing secrets MUST hard-fail the build; `sign=false` MUST skip signing even when secrets exist. Do NOT drop or merge any of the signing steps (sidecar + prewarm + native listener; NSIS; MSI; standalone `voice-typer-tauri.exe`; **the runtime-pack worker `voice-typer-worker-<triple>.exe` — added 2026-08-15 as the 5th binary per plan-runtime-pack-split §11.5**; and the full-offline installer when present). Do NOT remove the job-level `env:` mapping of `WIN_CSC_LINK` / `WIN_CSC_KEY_PASSWORD`, and do NOT replace it with a `secrets.*` reference inside a step `if:` condition.
Rationale: TX-23 — a misconfigured release must not silently ship unsigned (that's why sign=true + missing secrets hard-fails). S1-CR-99 — before the fix, the native listener, the MSI, and the standalone exe shipped UNSIGNED inside a signed installer; SmartScreen on Windows 11 flags unsigned binaries inside a signed installer ("Windows protected your PC" on first launch) and MSI/standalone users hit it on every install/launch. CRIT-7 — the `secrets` context is NOT populated in step `if:` conditions, so a gate on `secrets.X != ''` NEVER matches and signing is silently skipped; secrets must be mapped to job-level env first (empty on PR/fork builds → steps skip).
Applies to: All agents, all modes, all sub-agents.
```

```
C-CI-12
Rule: Do NOT remove `CLCACHE_DISABLE: "1"` from the job-level `env:` block of the `tauri-windows-build` job, and do NOT move it into a step-level `$env:CLCACHE_DISABLE = "1"` assignment only.
Rationale: S10-CC-1 — a step-level env var does NOT propagate to the Nuitka/SCons C compiler subprocess; with clcache enabled, torch module C compilation hangs indefinitely and the job times out (~90 min wasted). Job-level env is inherited by every subprocess and fixes the hang.
Applies to: All agents, all modes, all sub-agents.
```

```
C-CI-13
Rule: Do NOT rename the artifact names produced by `tauri-windows-build.yml` (`tauri-windows-installer`, `VoiceTyper-Tauri-MSI`, `VoiceTyper-Tauri-Sidecar-Binaries`, `VoiceTyper-Tauri-SHA256SUMS`, `tauri-binaries-manifest-windows`), and do NOT change the default binary filenames (`python-sidecar-<triple>.exe`, `prewarm-<triple>.exe`, `windows-key-listener.exe`, `voice-typer-worker-<triple>.exe` — the runtime-pack worker added by the pack split). New artifact names (e.g. `voice-typer-slim-core-<version>-<triple>.exe`, `voice-typer-runtime-pack-<pack-version>-<triple>.zip`, `pack-manifest.json` per plan §11.9) may be ADDED, but only if `tauri-build.yml`'s download steps are updated in the same commit.
Rationale: `tauri-build.yml`'s aggregate job downloads `name: tauri-windows-installer` by exact literal, and `tests/tauri/mig18/test_windows_signing.py` greps the default binary names — renaming breaks aggregation and/or the signing tests. If the aarch64 leg is ever enabled, arch-suffix the artifact names AND update tauri-build.yml's download steps in the same commit.
Applies to: All agents, all modes, all sub-agents.
```

```
C-CI-14
Rule: Do NOT revert the sidecar smoke-test step to a naive `$output = & $sidecar --version 2>&1` invocation. The launch MUST use .NET `ProcessStartInfo` + `Process.WaitForExit(180000)` with redirected stdout/stderr and a hard 180 s kill.
Rationale: The sidecar is frozen with `--windows-console-mode=disable`, making it a GUI-subsystem PE. PowerShell 7 does NOT wait for GUI-subsystem processes launched with `&` and never sets `$LASTEXITCODE` for them — the naive pattern yields empty output + a null exit code and ALWAYS fails the gate (reproduced with a minimal Nuitka onefile exe on pwsh 7.x). The .NET Process pattern is the only reliable way to capture exit code + stdout for GUI-subsystem binaries; the 180 s wait covers onefile bootloader extraction of the ~100 MB payload before argparse exits.
Applies to: All agents, all modes, all sub-agents.
```

```
C-CI-15
Rule: Do NOT remove the tauri-binaries.json recording + integrity gates in `tauri-windows-build.yml` (`update_tauri_manifests.py --triple <triple>` then `--check --triple <triple>`, plus the manifest artifact upload), and do NOT change the SLSA attestation gate (`github.event_name == 'workflow_dispatch' && inputs.sign == 'true'`).
Rationale: `tauri-binaries.json` is the integrity manifest the autostart launcher verifies fail-closed at login; the `--check` gate aborts the run on an empty/malformed hash so a bad manifest can never ship. The attestation gate must stay on workflow_dispatch + sign=true — the old `startsWith(github.ref, 'refs/tags/v')` gate never fired because the tag-push trigger is commented out (TX-24).
Applies to: All agents, all modes, all sub-agents.
```

---

## Category: Data & Privacy

```
C-DATA-1
Rule: Do NOT add network calls to the production code path UNLESS they fall into an explicitly allowed category: (1) cloud transcription / LLM providers the USER has configured and consented to (openai / groq / deepgram / custom `cloud_api_url` — see `cloud_engines.py` / `llm_polish.py`); (2) auto-update — "Check for Updates" / silent update check against the GitHub API (see `docs/auto-update-feature.md`); (3) model downloads (see ADR-0005, `docs/adr/0009-audio-filter-chain-architecture.md`); (4) the offline-pack (runtime pack) download from GitHub Releases — the one-time ~180-200 MB bundle containing the offline ASR engine (onnxruntime + ctranslate2 + faster-whisper + Parakeet ONNX + numpy/scipy), downloaded at most once per version and cached locally (see `docs/plan-runtime-pack-split.md` §4/§8.4, `docs/auto-update-feature.md`, `voice_typer/server/service/offline_pack.py`). Category (4) is permitted whether or not the code gates it behind consent: the `offline_pack_consent` toggle is a product/UX choice (currently default OFF — opt-in; the user may flip it to always-on for new installs), NOT a rule requirement. Anything NOT user-configured and user-initiated — telemetry, analytics, tracking, phone-home, or any other unsolicited egress — remains forbidden.
Rationale: The original wording ("no network call ever") predates cloud ASR/LLM engines and the auto-update feature, so agents applied it as a blanket offline ban and downgraded legitimate user-initiated features (e.g. "Check for Updates" CSP, cloud provider calls). The product promise is "no unsolicited phone-home", NOT "no network access ever". Agents that previously skipped, removed, or reworked network functionality citing the old wording MUST re-audit that work (search `worklog.md` / `review.md` for C-DATA-1 skips) and restore or improve anything that was downgraded. Category (4) was added by the USER on 2026-08-15: the pack download was previously covered only by analogy under category (3) "model downloads", and the user decided the offline-pack download is a legitimate allowed egress (like WebView2 / auto-updater runtime components) regardless of the consent toggle's default.
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

```
C-TEST-6
Rule (CLOUD-SANDBOX AGENT RULE): This rule exists FOR the cloud AI agent running inside the online platform's Linux Debian sandbox (the `/home/z/my-project` cloud workspace that hands back `changes.zip`) — the agent whose runs repeatedly completed, reported success, and shipped red test suites that only surfaced when the user applied the zip locally on Windows. Do NOT package deliverables (`changes.zip`), close a session, or mark any task/run complete while the current code state lacks a recorded GREEN full-suite run — 0 failed, 0 errors — covering the ENTIRE Python pytest suite AND the client vitest suite, plus the wiring trio (`cargo check`, `npm run typecheck:ci`, `pytest --collect-only`). Mandatory full-suite runs happen at exactly three points: (1) session-start baseline, (2) after every Implementation-Wave merge (orchestrator-owned; the wave is not done until green), (3) FINAL DELIVERY GATE on the exact final code state after the last fix and BEFORE packaging — any edit after a green run creates a new code state and VOIDS the evidence (re-run required). The 10-minute tool ceiling is NEVER an excuse to skip it: run the suite detached/backgrounded with output redirected to a log file and poll (`nohup python -m pytest tests/ -n auto --dist=loadgroup -q --no-cov --tb=no > /tmp/full_pytest.log 2>&1 &`), or split into per-domain chunks whose union provably covers 100% of collected tests (Σ chunk collected-counts == total from `pytest --co -q`; sum mismatch = gate failed). Sub-agents NEVER run the full suite (focused tests only; their 10-min ceiling cannot hold it) — aggregation is always the orchestrator's job. Manufacturing green is forbidden: no adding skips/xfail/pass-marks, no deleting/weakening tests, no excluding failing files from the chunk map — fix the root cause. Every full-suite run is recorded in `worklog.md`: commands, pass/fail counts, failing test IDs (or `0 failed`), OS qualifier. Respect the one-full-run-per-unchanged-state rule above — re-run only when the state actually changed.
Rationale: Cloud sessions repeatedly completed tasks, reported success, and shipped changes that introduced MANY test failures visible only in a full-suite run the agent never executed; the user's previously-green local suite broke on every apply. Focused/per-file greens prove only the slice — they say nothing about the system. The final delivery gate converts "the agent believes it works" into verified evidence before anything reaches the user's machine.
Applies to: All agents, all modes, all sub-agents. Runs are orchestrator-owned; sub-agents are bound by honest focused-test reporting and by the no-skip/no-xfail/no-delete prohibition.
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

```
C-TAURI-2
Rule: Do NOT change the `plugins` block of `src-tauri/tauri.conf.json` back to object values for unit-config plugins, and do NOT reintroduce a v1-style shell scope. The ONLY valid shapes (verified against the plugins-workspace v2 sources + tauri issue #8769 on the first successful host launch, 2026-08-21) are: `"single-instance": null`, `"notification": null`, `"dialog": null` (these plugins register NO config type — Tauri deserializes their entry into the serde UNIT type, so `{}` crashes startup with `PluginInitialization("...", "invalid type: map, expected unit")`), and `"shell": {"open": false}` (tauri-plugin-shell v2 accepts exactly ONE config key, `open`; a v1-style `{sidecar: true, scope: [...]}` block crashes startup with `unknown field 'scope', expected 'open'`). Sidecar spawn scoping in v2 is owned by the Rust host via `app.shell().sidecar(...)` (not ACL-gated); the JS-facing `shell:allow-spawn` capability grant in `capabilities/main-runtime.json` keeps its deny-all default scope. Do NOT "restore" scoping by moving sidecar entries into `plugins.shell` or into the capability permission as an allow-list without re-verifying against the plugin's actual Config struct.
Rationale: These four bugs made the shipped app IMPOSSIBLE to launch on any platform — every startup died on plugin-config deserialization. CI never caught it because CI builds but never RUNS the app (ADR-0020 §15 Phase 0-W host validation was still pending). Each failed startup cost a full diagnose cycle; the evidence tags are in git history (first Windows host run 2026-08-21).
Applies to: All agents, all modes, all sub-agents. Regression guards: `tests/tauri/mig19/test_final_glue.py::test_tauri_conf_unit_config_plugins_are_null` + the `test_tauri_conf_shell_config_is_v2_valid` tests in `tests/tauri/mig15|16|17|18`.
```

```
C-TAURI-3
Rule: Do NOT change the `dispatch` Tauri command's parameter shape away from FLAT `(cmd: String, data: Option<Value>)`, and do NOT introduce struct-typed parameters on ANY `#[tauri::command]` that the renderer invokes. Tauri v2 maps each JS `invoke()` key to a PARAMETER NAME — a single `args: DispatchArgs` param makes the host expect the top-level key `args`, and every renderer call fails with `invalid args 'args' for command 'dispatch': missing required key args`. The renderer contract is exactly `invoke('dispatch', { cmd, data })` (`python-namespace.ts` + allowlist.rs doc comment).
Rationale: Found on the first Windows host run (2026-08-21): the UI showed "Lost connection to Python backend" while the host↔sidecar WS link was perfectly healthy, because every renderer→host invoke died at arg-name matching. Renderer unit tests stub `invoke()` and can NEVER catch Rust-side arg-name drift — only a real host run (or an integration test driving the real command layer) can.
Applies to: All agents, all modes, all sub-agents. Guard comment lives inline at `commands/sidecar_cmds/dispatch.rs::dispatch`.
```

---

## Category: Rust Async & Tokio Runtime

```
C-TOKIO-1
Rule: Do NOT call `block_on` (any form: `tauri::async_runtime::block_on`, `tokio::runtime::Runtime::block_on`, `Handle::block_on`) inside a future that runs ON the tokio runtime — i.e. inside `tauri::async_runtime::spawn(async move { ... })` task bodies, `#[tauri::command]` async fns, or any `.await` chain. Inside async code, use `.await`, `futures_util::future::FutureExt::catch_unwind` for panic capture, or `spawn_blocking` for blocking work. A dedicated `std::thread::spawn` + `block_on` bridge is ONLY legal when the thread is NOT a runtime worker (see the sanctioned bridges in `sidecar/ws/respawn_scheduler.rs`, `sidecar/ws/heartbeat.rs`, `state.rs::on_host_exit` — each documents why the thread bridge is required). In particular, do NOT wrap the `initialize_sidecar` call in `main.rs` back into `std::panic::catch_unwind(|| ... block_on ...)`: that exact shape panicked at every startup with "Cannot start a runtime from within a runtime" until it was replaced with `AssertUnwindSafe(fut).catch_unwind().await` (fixed 2026-08-21).
Rationale: block_on inside a runtime worker panics instantly ("Cannot start a runtime from within a runtime") and kills the spawned task — the sidecar then never boots. Like C-TAURI-2, CI cannot catch it because CI never launches the app.
Applies to: All agents, all modes, all sub-agents. Guard comment lives inline at the spawn site in `src-tauri/src/main.rs`.
```

---

## Category: Sidecar WebSocket Handshake

```
C-WS-1
Rule: Do NOT reorder the three post-auth calls in `voice_typer/server/sidecar_ws.py::_handle_connection_inner`: `_install_subscriber` (subscribe only) → `_emit_ready_if_first` (publishes `ready`) → `_emit_initial_state_snapshot` (publishes `state_changed`). The initial `state_changed` snapshot MUST stay OUT of `_install_subscriber` and MUST be emitted AFTER `ready`. Symmetrically, do NOT weaken the Rust-side strictness in `src-tauri/src/sidecar/ws.rs::wait_for_auth_ok` (it accepts ONLY `auth_ok` / `ready` as the first post-auth frame — that strictness is a deliberate SEC decision so a compromised sidecar can't skip auth with an arbitrary first frame like `bubble_level`).
Rationale: The wire contract is "`ready` is the FIRST post-auth frame". When the snapshot lived inside `_install_subscriber`, it raced in ahead of `ready` and every Tauri handshake died with `WS auth unexpected frame type: state_changed`, triggering an endless supervisor respawn → full-app-relaunch loop. Reordering also risks re-breaking the older CR fix (ready published before the subscriber exists = ready lost entirely, UI never hydrates). Fixed 2026-08-21; guards: `tests/test_sidecar_ws_ready_ordering.py`, `tests/test_sidecar_ws_handle_connection_split.py`, `tests/test_sidecar_ready_emitted.py`.
Applies to: All agents, all modes, all sub-agents.
```

```
C-WS-2
Rule: Do NOT send sidecar→host WS frames as BYTES. `_safe_send` (and every other outbound path in `voice_typer/server/sidecar_ws.py`) MUST hand `websocket.send()` a **`str`** — the `websockets` library maps `str` → TEXT opcode and `bytes` → BINARY opcode, and the Rust host's reader parses TEXT only (binary frames are logged-and-dropped per C-WS-2 on the Rust side). Every dispatch response MUST also carry a numeric top-level `id` echoed from the request. Symmetrically, do NOT remove the host reader's Binary-frame WARN or its numeric-id pending resolution.
Rationale: Found on the first Windows host run (2026-08-21): `_safe_send` passed UTF-8 bytes to `websocket.send()`, so EVERY dispatch response left as a BINARY frame and was silently discarded inside the host — all renderer commands timed out ("Lost connection to Python backend") while heartbeat acks (sent inline as `str`) kept flowing. A test in `tests/test_sidecar_ws.py` had pinned the WRONG contract (`isinstance(payload, bytes)`); it now pins `str`. Guards: `tests/test_sidecar_ws_safe_send_text_frames.py` + the updated assertion in `tests/test_sidecar_ws.py::TestSafeSendSizeCapRegression`.
Applies to: All agents, all modes, all sub-agents.
```

```
C-WS-3
Rule: Do NOT enqueue a supervisor respawn request without carrying the requesting connection's WS generation (`Some(my_generation)` in `trigger_respawn_off_thread`), and do NOT remove the dequeue-time staleness re-check in the supervisor loop / one-shot fallback (`respawn_scheduler.rs`). Only heartbeat-liveness and auth-failure paths may pass `None`.
Rationale: Reader/writer cleanup blocks decide "connection died → respawn" synchronously but EXECUTE the request asynchronously via the supervisor queue. Without a generation re-check at dequeue time, a stale decision lands AFTER a newer reconnect went live and kills the healthy new connection — observed 2026-08-21 as an infinite kill/restart ping-pong (~1 respawns/sec) started by a single external sidecar kill.
Applies to: All agents, all modes, all sub-agents.
```

---

## Category: Local Tauri Dev (Windows, no MSVC)

```
C-TDEV-1
Rule: Do NOT assume `cargo tauri dev` needs MSVC on this machine, and do NOT delete the GNU-toolchain plumbing: `src-tauri/.cargo/config.toml` (rust-lld linker wrapper at `.cargo-tmp/linker-wrap.exe`), the MSYS2 mingw64 dependency (`x86_64-w64-mingw32-gcc-ar.exe` on PATH), the default `stable-x86_64-pc-windows-gnu` rustup toolchain, and the generated binary stubs from `python scripts/gen_tauri_icons_stub.py` (satisfies `bundle.externalBin` + `bundle.resources`; they are FAKE binaries that exit 1 — never ship them). Dev launches MUST set `VOICE_TYPER_SIDECAR_DEV=1` (ADR-0020 §14) — without it the host spawns the frozen-release externalBin path and gets os error 216 from the stub exe, followed by an infinite supervisor respawn loop.
Rationale: The machine has no Visual Studio C++ Build Tools; the whole GNU setup is what makes local Tauri builds possible (~80s full compile, deps cached). Working dev recipe (from repo root): terminal A `python -m http.server 1420 --bind 127.0.0.1 --directory voice_typer/client/out/renderer` (devUrl); terminal B `npm run build:renderer` after UI edits (Ctrl+R in-app to reload); terminal C `$env:VOICE_TYPER_SIDECAR_DEV="1"; npx @tauri-apps/cli dev --config <override-json-blanking-beforeDevCommand>`. The stock `beforeDevCommand` (`cd voice_typer/client && npm run build:renderer`) currently fails under the tauri CLI with "The system cannot find the path specified" (CLI spawn CWD mismatch — known open follow-up; the pinned literal is asserted by `tests/tauri/mig19/test_final_glue.py`, so fixing it means updating that test in the same commit).
Applies to: All agents, all modes, all sub-agents.
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
Rule: Do NOT remove the space-separated `<duration>` suffix from lifecycle-completion log lines. Every log line that reports the END of a timed operation (startup complete, model/VAD/CUDA-DLL load, warm-up inference, transcription, recording stop, and any future timed load/transaction) MUST carry a duration suffix produced by `format_duration()` in `voice_typer/server/duration.py` (dependency-free; import it rather than inlining ad-hoc `f"... {x:.1f}s"` strings that drift from the minutes case): ` 2.3s` for sub-minute durations, ` 1m 2.3s` for anything longer — `format_duration()` returns the duration WITH a single leading space, so callers splice it directly with a bare `%s` and MUST NOT add their own space before the placeholder (a preceding space would render a double space). Never glue the duration to the text with no separator, never `took=2.3s`, never `-- 2.3s` — the space-separated suffix is the canonical, greppable performance marker. The suffix is attached to the timed event, normally at line END; the recording line is the one intentional mid-line placement (`Recording stopped 30.0s of audio, ...` reads naturally — the duration IS the subject). Timed lines today: `[STARTUP] Startup complete (model still loading in background) 3.7s`, `[MODEL] Model loaded via ... 1.4s`, `[PERF] Warm-up inference completed — CUDA kernels primed 2.4s`, `[CUDA-DLL] Prepended to PATH: [...] 0.8s`, `[TRANSCRIBE] Transcription complete (len=..., cycle=...) 0.8s`, `[DICTATION] Recording stopped 30.0s of audio, ...`, `[VAD] Silero VAD model preloaded + warmed 1.2s`. The measurement source is always `time.perf_counter()` (monotonic) captured at the start of the operation and diffed at the completion log. Grep anchor: `\d+(m \d+)?\.\ds$` at line end.
Rationale: Added 2026-08-08 so performance is measurable at a glance in the log file — how long startup took, how long the model/packages took to load, how long transcription took, and how long the user recorded. The suffix originally used a leading underscore (`_2.3s`); the user requested the space form (` 2.3s`) on 2026-08-15 and it was changed project-wide, logs and rule together. Prior to the convention, several completion lines had no duration at all (startup) or used inconsistent ad-hoc formats (`took=%.1fs`, `— %.1fs`, `-- %.1fs of audio`) that could not be grep-summed. Reverting to a duration-less or non-space format regresses the performance observability the user explicitly requested.
Applies to: All agents, all modes, all sub-agents.
```

---

## Category: Models Page UI

```
C-MODELS-1
Rule: Do NOT remove or weaken the bordered card treatment on the Models page segmented control, and do NOT apply it to only one layer. BOTH layers MUST carry the same card/surface border token (`border border-border/10`): (a) the OUTER parent container that directly holds the Local/Cloud options — the `SegmentedControl` root (role="tablist") with `rounded-lg border border-border/10 bg-(--bg-subtle)` in `voice_typer/client/src/renderer/src/pages/Models.tsx` — and (b) the ACTIVE option's indicator (`tabPageIndicatorClassName` = `bg-(--bg) border border-border/10` in `_tabBarStyles.ts`). The tabs variant in `segmented-control.tsx` MUST NOT re-add `border-none` (its `border-style: none` silently cancels the container border — tailwind-merge treats `border` and `border-none` as different groups, so both classes survive). Do NOT introduce a new border color.
Rationale: The segmented control must read as one bordered card among the model cards — both its outer container and its active option must share the card border. Established 2026-08-21; a border on only the active option left the container looking like a borderless strip.
Applies to: All agents, all modes, all sub-agents.
```

```
C-MODELS-2
Rule: Do NOT let model-size download buttons resize to their content, center their content, or use an oversized icon. Every model-size Download button MUST use the shared tokens exported from `voice_typer/client/src/renderer/src/components/models/ModelCardActions.tsx`: fixed width `DOWNLOAD_SIZE_BUTTON_WIDTH` (`w-[88px]`), left alignment `DOWNLOAD_CONTENT_ALIGNMENT` (`justify-start` — icon + size text share one start position across every row, overriding the Button base's `justify-center`), and the compact icon size `DOWNLOAD_ICON_CLASS` (`h-3.5 w-3.5`) so the download icon visually matches the 11px size text. Do NOT hardcode per-model widths, do NOT center the content, do NOT reintroduce the 16px `h-4` icon, and do NOT remove the `~`-stripping / number+unit-spacing normalization in `formatModelSize` (`voice_typer/client/src/renderer/src/lib/utils/models.ts`). Sizes must render as `75 MB` / `3 GB` / `809 MB` — no `~`, space always present. The "Download Deps" button (a label, not a size) is exempt from the fixed width but keeps the same left alignment.
Rationale: Content-fitted widths and centered content misaligned the download buttons across model rows, and a 16px icon dominated the 11px size text. Fixed identical width + left-aligned content + a balanced 14px icon keep every model's Download button consistent, aligned, and proportional.
Applies to: All agents, all modes, all sub-agents.
```

```
C-MODELS-3
Rule: Do NOT flatten the model-card metadata line back into one uniform gap. The metadata must stay two independent groups: the information group (VRAM/WER `MetadataPair`s, spaced `gap-x-3` in `ModelVariantRow`) and the label group (all `MetadataTag` badges, wrapped in a `<span className="inline-flex flex-wrap items-center gap-x-1.5 gap-y-1.5">` in `LocalModelsPanel`'s `ModelMetadataLine`). Keep `gap-x-3` between the WER pair and the first label; keep `gap-x-1.5` between the labels themselves.
Rationale: Uniform `gap-x-3` spread the descriptive badges (Multilingual / Fast Speed / Distilled) as far apart as the VRAM/WER metrics, breaking the "two groups" reading. The two-group structure was established 2026-08-21.
Applies to: All agents, all modes, all sub-agents.
```

```
C-MODELS-4
Rule: Do NOT replace the provider/family expand/collapse affordance with a minus icon, a chevron, a rotating icon, or any plus-to-other-symbol transition. The accordion trigger icon in `voice_typer/client/src/renderer/src/components/ui/accordion.tsx` MUST be a single persistent `PlusSignIcon` that stays a `+` in BOTH the collapsed and expanded states, with `data-slot="accordion-trigger-icon"` + `aria-hidden="true"` and NO icon-state animation classes (the Radix trigger owns `aria-expanded`). Clicking the icon still expands/collapses the provider — only the icon's appearance is intentionally constant.
Rationale: A static plus in both states is the user's explicit design decision (2026-08-21). Earlier iterations that swapped `+`→`−` or animated a rotating chevron were reverted; the affordance is deliberately identical whether the group is open or closed.
Applies to: All agents, all modes, all sub-agents.
```

---

## Category: Sidebar & Title Bar

```
C-SIDEBAR-1
Rule: Do NOT add application branding (logo/icon + app-name text) back to the sidebar. The sidebar (`voice_typer/client/src/renderer/src/components/layout/Sidebar.tsx`) is NAV-ONLY: its first child is the navigation scroll container (`<nav aria-label="Main navigation">`), and the navigation fills the full sidebar height. The `Logo` component must not be imported/rendered by `Sidebar.tsx`, and no element in the sidebar may carry `APP_NAME` as a label.
Rationale: The branding header was deliberately removed (2026-08-22) so the `MAIN` / `POWER FEATURES` / `SYSTEM` navigation groups fill the space the header previously occupied. Re-adding a logo/title block reintroduces reserved height above the nav and breaks the layout contract tests (`SIDEBAR-BRANDING` assertions in the Sidebar suites).
Applies to: All agents, all modes, all sub-agents.
```

```
C-SIDEBAR-2
Rule: Do NOT render a theme switcher anywhere except the title bar, and render EXACTLY ONE. The icon-only theme control lives in `voice_typer/client/src/renderer/src/components/layout/TitleBar.tsx` inside the window-control cluster (immediately LEFT of minimize/maximize/close on Windows/Linux; anchored at the bar's right edge on macOS). Do NOT leave a second visible copy in the sidebar or anywhere else in the UI, and do NOT move the control out of the title bar.
Rationale: The theme control was moved from the sidebar's bottom row into the title bar beside the Windows window controls (2026-08-22) so it reads as part of the window's control cluster. A duplicate anywhere else (sidebar, footer, page header) fragments the single source of truth and contradicts the layout contract tests (`SIDEBAR-BRANDING`/`TitleBar — theme control`).
Applies to: All agents, all modes, all sub-agents.
```

```
C-SIDEBAR-3
Rule: Do NOT build a second theme implementation for the title-bar control, and do NOT change the theme's Light/Dark/System cycling behavior or the underlying theme state management while modifying its presentation. The title-bar theme control MUST be the shared `ThemeSwitch` component (`voice_typer/client/src/renderer/src/components/layout/ThemeSwitch.tsx`) — icon-only, no visible text label, driven by the SAME `themeMode` + `onThemeChange` values App.tsx derives from `useTheme` and passes as props. The `title` + `aria-label` ("Current theme: {mode}. Click to switch to {next}.") are the ONLY way the current/next modes are exposed (no text in the UI).
Rationale: The theme control is presentation-only; the cycling logic (`nextMode`), the `useTheme` singleton store, the debounced backend save, and the Light/Dark/System semantics are shared application state that must not be duplicated or re-implemented per-surface. A parallel implementation would drift (different icons, labels, or cycle order) and silently desync the UI from the persisted config.
Applies to: All agents, all modes, all sub-agents.
```

```
C-SIDEBAR-4
Rule: Do NOT let sidebar icons shift horizontally during expand/collapse. Every top-level nav button (leaves AND the Settings parent) MUST anchor its icon through the single icon column — container `p-2` + `border-s-2` + button `px-2` = 18px from the aside edge in BOTH states — and the collapsed rail width (`w-13`, 52px) MUST keep that column centered. Never use `justify-center` (or any per-state padding swap) to center collapsed icons.
Rationale: The original collapsed Settings trigger used `justify-center p-2` while leaves used `px-2`/`px-3`, so icons sat at different x-positions per state and per item — the whole column visibly jumped on every toggle (verified 2026-08-24: icons pinned at x=18 in both states via the a11y bounds).
Applies to: All agents, all modes, all sub-agents.
```

```
C-SIDEBAR-5
Rule: Do NOT instant-hide, unmount, or branch-swap sidebar label text on collapse. Item/parent labels MUST render through the shared animated visibility transition (`navTextClasses` in `Sidebar.tsx`: `max-width` + `opacity` + inline-start `translate` (RTL-mirrored) + `filter` with EXPLICIT `blur-[0px]`/`blur-[4px]` endpoints — never `filter-none`, which cannot interpolate against `blur(N)`), synchronized with the aside's 200ms ease-out width transition. Group headings MUST collapse via `max-height`+`opacity` (never an instant conditional unmount, which shifts the groups below). Buttons clip their own row (`overflow-hidden`) so the animating label never paints outside it.
Rationale: The original text snapped (non-interpolable `filter: none` ↔ `blur(4px)`, no translate, headings unmounting and jumping the nav up one heading height per group at toggle time). One coherent 200ms ease-out model — width + labels + headings — replaced the competing sources (2026-08-24).
Applies to: All agents, all modes, all sub-agents.
```

```
C-SIDEBAR-6
Rule: Do NOT revert the sidebar's deliberate vertical rhythm: items breathe with `gap-1` inside a group section; the nav separates groups with `gap-5` expanded (headings visible) / `gap-2` collapsed rail (headings at `max-h-0` still contribute their surrounding flex gaps, netting 16px cluster separation vs the 4px item rhythm). Do not flatten both states to one gap value or leave collapsed clusters flush-with-huge-voids.
Rationale: The original collapsed rail had 0px gaps inside groups and 24px voids between them (a vertically compressed sidebar, not a designed rail). The two-scale rhythm (4px items / 16px clusters collapsed; 4px items / heading-separated groups expanded) is intentional (2026-08-24).
Applies to: All agents, all modes, all sub-agents.
```

```
C-SIDEBAR-7
Rule: Do NOT reorder the sidebar's navigation hierarchy (Main: Home/History/Analytics → Power features: Templates/Vocabulary/Models/Microphone → System: Settings/About/Privacy) without product-level justification. Frequently used destinations stay toward the top; low-priority informational/system destinations stay grouped at the bottom.
Rationale: The three-group hierarchy encodes usage frequency (day-to-day pages, power features, system/info) and is pinned by the grouping tests; reordering for visual novelty breaks discoverability and the mental model.
Applies to: All agents, all modes, all sub-agents.
```

```
C-SIDEBAR-8
Rule: Do NOT implement expand/collapse behavior through page-specific patches or duplicated per-state button trees. The expanded and collapsed sidebar MUST render through the single shared source of truth in `Sidebar.tsx` (shared `navTextClasses` helper, ONE parent-button element wrapped by Popover-flyout vs inline-Collapsible, per-state spacing only where the rhythm requires it). Every collapsed rail icon MUST keep a non-empty accessible name (mounted label span) and the same right-side `HotkeyTooltip` treatment as the leaves.
Rationale: Two different button trees for the collapsed/expanded Settings parent popped its label instantly and dropped its accessible name entirely (empty accessible name on the flyout trigger). The shared-element structure is what makes the label transition, a11y name, and icon anchoring hold in both states (2026-08-24).
Applies to: All agents, all modes, all sub-agents.
```

```
C-SIDEBAR-9
Rule: Do NOT give active sidebar pages a custom or stronger border. The active page item (top-level leaf AND Settings submenu child) MUST use the standard card treatment: `border-border/10` (the same ~10%-opacity card border token every card in the app uses) + `bg-(--bg)` + `text-(--text-primary)`. The legacy `border-s-2`/`border-s-transparent` alignment borders are REMOVED — do not reintroduce them. The Settings PARENT is exempt: when its submenu is open it gets ONLY the calm foreground treatment (`text-(--text-primary)` + `font-medium` + `hover:bg-foreground/5`) — never the card border/background.
Rationale: The active item previously had no border at all while every card surface in the app carries `border-border/10`; the parent must not compete with its active child (supersedes the older UX-16 "no border, transparent border-s-2 only" contract, 2026-08-24).
Applies to: All agents, all modes, all sub-agents.
```

```
C-SIDEBAR-10
Rule: Do NOT swap the Settings expand/collapse arrow icon. ONE persistent `ArrowRight01Icon` glyph renders in a `ms-auto` (far-end edge) `size-6` aria-hidden wrapper; direction is animation only: `rotate-90` when the submenu is open, `nav-directional-icon` (RTL mirror) when closed, transitioned via `transition-[rotate]` (NOT `transition-transform`, which would visibly animate the RTL mirror flip). The arrow renders only in the expanded sidebar (the collapsed rail opens the flyout instead).
Rationale: Conditional ArrowRight/ArrowDown rendering made the indicator pop; rotation is the app's animation convention. `ms-auto` pins the indicator to the row's end padding instead of hugging the label (2026-08-24).
Applies to: All agents, all modes, all sub-agents.
```

```
C-SIDEBAR-11
Rule: Do NOT decouple the Settings submenu's open state from navigation. The submenu is open EXACTLY while a Settings sub-page (or the legacy `settings` parent literal) is the current page (`expanded = hasActiveChild || isParentActive`) and MUST close when the user navigates anywhere else. There is NO persisted manual preference (the old `vt_settings_submenu_expanded` localStorage key is removed) — navigating back to Settings re-opens the submenu automatically.
Rationale: The old manual flag survived navigation, leaving the submenu stuck open on unrelated pages; navigation itself is now the single source of truth (2026-08-24).
Applies to: All agents, all modes, all sub-agents.
```

```
C-SIDEBAR-12
Rule: Do NOT let sidebar icons shift horizontally during expand/collapse, and do not center them per-state. Every top-level nav button anchors its icon through the single icon column — container `p-2` + the Button base's uniform 1px border + button `px-2` (17px from the aside edge) in BOTH states — and the collapsed rail width (`w-12`, 48px) keeps that column centered. Never `justify-center`, never per-state padding swaps. (Supersedes the C-SIDEBAR-4 arithmetic — `border-s-2` 18px / `w-13` — which the C-SIDEBAR-9 card-border change removed; the INVARIANT — identical icon x-position in both states, centered collapsed column — is unchanged and still pinned.)
Rationale: Per-state padding/wrapper differences made icons jump on every toggle; the anchored-column math must stay symmetric across states (re-verified 2026-08-24 via a11y bounds).
Applies to: All agents, all modes, all sub-agents.
```

```
C-SIDEBAR-13
Rule: Do NOT remount nav buttons when the sidebar toggles. The element carrying the label transition must persist across states, so every nav button renders through the SAME wrapper element type in both states: leaves are ALWAYS wrapped in `HotkeyTooltip` (expanded passes its `disabled` prop to suppress the tooltip content), and the Settings subtree ALWAYS renders inside ONE `Popover.Root` (parent button in a `Popover.Anchor`; only the CONTENT branch differs — portal flyout collapsed vs inline Collapsible expanded). Never branch the wrapper type (Fragment↔Tooltip, Popover↔Collapsible) — a wrapper-type change remounts the button and CSS transitions do not run on freshly mounted nodes, so labels snap instead of animating. Labels animate only via `navTextClasses` (opacity + inline-start translate + explicit blur endpoints + max-width), never `display:none` or conditional unmount.
Rationale: The original per-state wrapper swap was the root cause of "labels disappear almost instantly" while section headings (stable tree) animated fine (diagnosed + fixed 2026-08-24).
Applies to: All agents, all modes, all sub-agents.
```

```
C-SIDEBAR-14
Rule: Do NOT push the System group to the bottom with spacer elements, fixed heights, or large static gaps. The System/low-priority cluster is pinned to the sidebar's bottom edge via `mt-auto` (flex auto margin) on its `<section>`, inside the `min-h-full flex-col` nav — on short windows the auto margin collapses to 0 and the nav simply scrolls. Main + Power features stay at the top.
Rationale: The importance hierarchy (frequent destinations top, system/info bottom) must come from the layout structure so it stays stable across window sizes and both sidebar states (2026-08-24).
Applies to: All agents, all modes, all sub-agents.
```

```
C-SIDEBAR-15
Rule: Do NOT reintroduce separate About and Privacy pages/destinations. They are ONE combined destination: Page literal `aboutAndPrivacy`, route key `aboutAndPrivacy`, component `pages/AboutAndPrivacy.tsx` (default export `AboutAndPrivacyPage`), nav/i18n key `nav.aboutAndPrivacy` (display "About & Privacy"), icon `ShieldUserIcon` (shield + user = personal-data protection; never a warning/error-semantics glyph). The old `about`/`privacy` Page literals are removed from the union — do not restore them.
Rationale: Two short, low-traffic informational pages read better as one "what the app is + how it treats your data" story; duplicate destinations after a merge are a navigation defect (merged 2026-08-24).
Applies to: All agents, all modes, all sub-agents.
```

```
C-SIDEBAR-16
Rule: Do NOT make the collapsed-rail Settings flyout keyboard-hostile. The flyout child buttons render in NATURAL tab order (no `tabIndex={-1}` — the portal sits outside the nav's roving-tabindex scope, so Tab is the only keyboard path in), and the collapsed Settings trigger KEEPS the roving tab stop (`tabIndex=0`) even while a Settings sub-page is active — otherwise every rail button is -1 and Tab skips the entire sidebar.
Rationale: Audit finding (2026-08-24): hard-coded `-1` flyout children + portal-outside-nav scope left the Settings sub-pages unreachable by keyboard from the collapsed rail (WCAG 2.1.1), and the active-child roving rule zeroed out the whole collapsed nav.
Applies to: All agents, all modes, all sub-agents.
```

```
C-SIDEBAR-17
Rule: Do NOT derive the Settings submenu's open state purely from the current page, and do NOT keep multiple sources of truth. There is exactly ONE state (`submenuOpen`, initialized from whether a Settings page is active) with exactly three deterministic transitions: (1) clicking the parent TOGGLES it — open → closed in place (even on a Settings sub-page, with no navigation fired), closed → open + navigate to the default child; (2) ENTERING the Settings section by any path (parent click, Ctrl+,, tray/back-forward navigation) reveals it via a sync effect; (3) LEAVING the section closes it. No persisted preference, no timeouts, no navigation side effects for closing. (Refines C-SIDEBAR-11: the submenu may now be toggled closed while a Settings sub-page is active — when that happens the PARENT carries `aria-current="page"` and the roving tab stop, because the active leaf is not rendered.)
Rationale: The previous derived model made the parent click a no-op re-navigation — the submenu could never be closed by clicking; the toggle must be deterministic against the navigation sync (verified 2026-08-25).
Applies to: All agents, all modes, all sub-agents.
```

```
C-SIDEBAR-18
Rule: Do NOT swap, unmount, or branch-replace any sidebar element at the moment the sidebar collapses or expands. The Settings Collapsible renders UNCONDITIONALLY in both sidebar states (`open={submenuOpen && !collapsed}`) so collapsing the rail ANIMATES an open submenu closed in sync with the width transition instead of vanishing it; the flyout Portal is strictly additive; the Settings chevron is rendered in BOTH states and FADES (`transition-[opacity]` + `opacity-0 pointer-events-none` collapsed) instead of unmounting. Group headers follow the SAME text motion model as nav-item labels — a `max-height`-only container (space collapse) with the label text on an inner `block` span carrying the shared horizontal motion (`navLabelMotion`: opacity + inline-start translate + explicit blur endpoints, X-axis only, never any Y component), with the text fade slightly faster (150ms) than the container collapse (200ms) so the shrinking clip never visibly half-cuts glyphs. (Extends C-SIDEBAR-5/10/13.)
Rationale: The per-state branch swap unmounted the open submenu instantly at toggle time; the header's combined max-height+opacity transition vertically clipped the label mid-transition; the chevron popped out of the DOM (all fixed 2026-08-25).
Applies to: All agents, all modes, all sub-agents.
```

```
C-SIDEBAR-19
Rule: Do NOT write tooltip copy that is verbose, explanatory, or mechanism-describing when a concise action word suffices. The title-bar Back and Forward tooltips are EXACTLY `Back` and `Forward` (localized per-locale to the same verb root as that locale's `a11y.goBack`/`a11y.goForward`), rendered with their shortcut chips via `HotkeyTooltip` — no "(or mouse back button)"-style wording, no "button", no parentheticals. The `titleBar.backWithShortcut`/`titleBar.forwardWithShortcut` keys are REMOVED (the chips come from `HotkeyChips`, not the label string) — do not reintroduce them or duplicate tooltip strings elsewhere.
Rationale: Tooltips must name the action directly; mechanism wording ("or mouse back button") was noise duplicated across all 8 locales and the dead *WithShortcut keys invited drift (cleaned 2026-08-25).
Applies to: All agents, all modes, all sub-agents.
```

```
C-SIDEBAR-20
Rule: Do NOT restore the three-group sidebar hierarchy. The sidebar has exactly TWO groups (user product decision 2026-08-25, supersedes the C-SIDEBAR-7 group order): (1) a header-less top group (`hideLabel`, aria-label "Main" preserved for AT) containing Home, History, Analytics, Models, Templates, Vocabulary — the former "Power features" group and its `nav.group.power` i18n key are REMOVED; (2) the System group (visible heading, `mt-auto`-pinned bottom) containing Settings, Microphone (input-device configuration belongs beside app settings), About & Privacy. Do not reintroduce a "Power features" heading, a third group, or the old item-to-group assignments.
Rationale: The three-group split left a two-item delta between groups and a heading that added no information; the user consolidated to a header-less primary list plus a system cluster (2026-08-25).
Applies to: All agents, all modes, all sub-agents.
```

---

## Category: Homepage Status Pill & Description Synchronization

```
C-HOME-1
Rule: Do NOT let the Home page status pill and its dynamic description line below the mic button derive from different sources of truth. Both MUST be derived from the same authoritative `{recordingState, lastError}` pair hydrated by `applyStatusWithReason` in `voice_typer/client/src/renderer/src/hooks/useConnection.ts`: EVERY renderer sync path (`status_change`, the connect-time `state_changed` snapshot, and both `get_status` catch-ups — initial probe + background reconnect) must write recordingState and lastError TOGETHER from the same payload's `{status, message}` tuple, and the pill key (`statusKeyFor` in `pages/home/lib/status.ts`) must surface ERROR only when the description line actually shows an error. An ERROR pill displayed above the normal `Press <hotkey> or click to dictate` hint — or any pill/description disagreement across startup, model install/removal, navigation back to Home, or recovery — is a violation of this invariant.
Rationale: The backend emits ONE tray-state tuple `set_state(state, message)`; the "no model installed" ERROR state carries its reason in that message. Sync paths that updated only `recordingState` (the old `state_changed` handler unconditionally cleared `lastError`; the old `get_status` responses/catch-ups carried no `message`) made the homepage error message intermittent — it appeared only when a live `status_change` happened to deliver the error, and vanished whenever the app launched/reconnected while the backend was already in error.
Applies to: All agents, all modes, all sub-agents.
```

---

## Category: Persistence & Data Files

```
C-PERSIST-1
Rule: Do NOT flatten or merge the vocabulary persistence model. `vocabulary.json` (`voice_typer/server/vocabulary.py`) is the AUTHORITATIVE store for USER vocabulary customizations ONLY — it persists the diff against the bundled defaults in `corrections.json` plus the reserved `_deleted` tombstone map (entries removed from the bundled set). Bundled entries are intentionally NOT duplicated into `vocabulary.json`; the merged vocabulary the UI/dictation sees is `bundled + user - _deleted`. A user file that contains only `_deleted` is correct (the visible entries are bundled defaults), NOT a sign of missing data. The 6 category buckets (`misspellings`, `phrase_corrections`, `extra_word_patterns`, `technical_terms`, `names`, `products`) are the PERSISTED DATA LAYER — the renderer hides them behind a flat original→corrected list (auto-assigned via `detectCategory`), but the backend applies per-category, the usage tracker keys by category, and the diff-save/tombstones are per-category. Do NOT remove the category buckets without migrating `apply_to_text`, `CorrectionUsageTracker`, and `save_vocabulary_with_diff`.
Rationale: Audited 2026-08-22 — every category is consumed by the apply engine, usage tracking, and diff-save; the flat UI is a presentation choice, not a schema change. Removing them would break the vocabulary feature.
Applies to: All agents, all modes, all sub-agents.
```

```
C-PERSIST-2
Rule: Do NOT merge `correction-usage.json` into `vocabulary.json` (or any other file). `correction-usage.json` (`voice_typer/server/correction_usage.py`, `CorrectionUsageTracker`) is INDEPENDENT ANALYTICS / time-series data — per-(category, original) cumulative counts + per-local-day correction/dictation totals feeding the Vocabulary page's "used Nx" and the Analytics corrections-rate. It has a different producer (the dictation engine), a different lifecycle (batched debounced writes, 90-day prune, prune-on-delete), and is NOT vocabulary data.
Rationale: Audited 2026-08-22 — forcing a merge to reduce file count would couple two independent lifecycles and corrupt both stores.
Applies to: All agents, all modes, all sub-agents.
```

```
C-PERSIST-3
Rule: Do NOT remove `recovery.json`. It is an ACTIVE crash-recovery store for the last `MAX_RECOVERY_ENTRIES` UNPASTED transcriptions (`voice_typer/server/crash_recovery.py`, `CrashRecovery`): the dictation pipeline calls `add()` (gated by `config.crash_recovery_enabled`), startup calls `check_on_startup()` to notify the user of recovered text, and diagnostics export reads it. An empty `{"entries": []}` is the NORMAL state (nothing pending), not a signal of obsolescence.
Rationale: Audited 2026-08-22 — the recovery mechanism is live end-to-end (pipeline write → startup check → tray notify).
Applies to: All agents, all modes, all sub-agents.
```

```
C-PERSIST-4
Rule: Do NOT merge `restart_history.json` and `restart_counter.json`. They are two INDEPENDENT per-runtime restart mechanisms with different schemas, semantics, and lifecycles: `restart_history.json` is the ELECTRON-only production app-relaunch crash-loop breaker (array of epoch-ms timestamps, 60s window, cap 3; `voice_typer/client/src/main/python/relaunch-app.ts`); `restart_counter.json` is the TAURI-only sidecar-respawn circuit breaker (`{"count", "ts"}` with a 10-minute staleness window, cleared on successful reconnect; `src-tauri/src/sidecar/supervisor.rs`). The two runtimes never coexist and neither reads the other's file — merging them would force two independent circuit breakers to share one incompatible schema.
Rationale: Audited 2026-08-22 — the two files complement rather than duplicate; a merge would break both circuit breakers and lose per-runtime semantics.
Applies to: All agents, all modes, all sub-agents.
```

---

## Category: Microphone Selection & Consent Flow

```
C-MIC-1
Rule: Do NOT make any microphone other than System Default the initial selection on first use. `config.microphone` MUST default to `null` (= System Default) on fresh installs, and every consumer (recorder init, level monitor, microphone test, tray) must treat `null` as "OS default input", never as an error state or a coerced concrete device.
Rationale: Established 2026-08-24 during the Microphone-page revamp — only two code paths may write `config.microphone` (onboarding + explicit user selection via settings IPC); nothing may auto-populate it.
Applies to: All agents, all modes, all sub-agents.
```

```
C-MIC-2
Rule: Do NOT silently overwrite a user's selected microphone with `Unknown` or another device. A persisted selection that no longer matches an enumerated device must resolve through the shared id resolvers (`find_microphone_by_id` / `resolve_mic_id_to_device_index` in `voice_typer/server/server_platform/microphone_list.py` — stable ids are `"<hostapi>|<name>[#N]"`, legacy bare-index and `<index>|<name>` compound strings stay resolvable), and when genuinely unresolvable fall back to System Default WITH user-visible feedback (warning snackbar + recovery banner), never a silent mislabel.
Rationale: Persisting raw PortAudio indices made selections go stale across reboots/hot-plugs and rendered "Unknown"; stable ids plus the explicit fallback contract fixed the class of bug (2026-08-24).
Applies to: All agents, all modes, all sub-agents.
```

```
C-MIC-3
Rule: Do NOT show persistent prerequisite warnings for consent-gated actions. Consent-protected features (voice biometrics, HuggingFace download, cloud providers, LLM polish, offline pack) MUST use the shared just-in-time gate — `openConsentGate()` (`lib/consentGate.ts`) + the single `ConsentGateDialog` mounted in App.tsx — invoked AT THE MOMENT the user attempts the protected action, with `onAllow` continuing that action after grant. Settings toggle rows and the onboarding ConsentStep are the only legitimate always-visible consent surfaces. Do not create feature-specific consent modals.
Rationale: Persistent "enable consent" banners nag users who never perform the action and duplicate the unified gate migrated app-wide 2026-08-24; point-of-use asking preserves GDPR Art. 9 requirements while keeping the flow actionable.
Applies to: All agents, all modes, all sub-agents.
```

```
C-MIC-4
Rule: Do NOT make microphone quality/filter presets multi-select or reintroduce a dropdown on the Microphone page. Presets are mutually exclusive and MUST be chosen through the shared accordion+RadioGroup pattern (`PresetAccordionSelector` using `ui/accordion` + `ui/radio-group`); the collapsed header shows the current selection. The microphone test duration is FIXED at 10 seconds (`MICROPHONE_TEST_DURATION_SEC`) and MUST NOT become user-configurable again without an explicit product decision reversing this rule.
Rationale: Fixed during the 2026-08-24 revamp — a single-source constant removed dead configurability; radio/accordion matches the app's interaction language where a dropdown did not.
Applies to: All agents, all modes, all sub-agents.
```

```
C-MIC-5
Rule: Do NOT hide invalid audio devices with UI-only filters. Non-user-selectable devices (placeholder endpoints like `Input ()`, empty/whitespace names, generic-label-only entries) MUST be filtered at the enumeration source (`_is_invalid_device_name` in `voice_typer/server/server_platform/remote_session.py`, applied inside `list_microphones`), so every consumer (tray, recorder, tests, renderer) sees the same clean set.
Rationale: A renderer-only filter would leave the bogus device selectable via tray/tests and would reappear wherever a new consumer enumerates devices (fixed at source 2026-08-24).
Applies to: All agents, all modes, all sub-agents.
```

```
C-MIC-6
Rule: Do NOT introduce unrelated styling on the Microphone page. Cards use the standard tokens (`border border-border/10`, `bg-(--bg-subtle)`, `text-(--text-primary)` / `text-(--text-muted)`); microphone selection uses RadioGroup rows (System Default row first) — no bright-blue full-card borders, no verbose per-row action buttons, no technical channel/rate metadata in user-facing rows, section labels use one consistent treatment.
Rationale: Pinned by the 2026-08-24 revamp so the page stays inside the existing Voice Typer design system; visual drift here was the original defect class.
Applies to: All agents, all modes, all sub-agents.
```

```
C-MIC-7
Rule: Do NOT "deduplicate" microphones by display name alone, and do NOT reintroduce per-host-API duplicate records. `list_microphones()` MUST return the canonical host-API view (Windows → WASAPI, macOS → Core Audio, Linux → PulseAudio when present), with graceful fallback to the unfiltered list when the preferred host API enumerates zero devices. PortAudio exposes every endpoint once per host API (MME/DirectSound/WASAPI/WDM-KS on Windows — 4 views of the same device); the canonical-API view IS the device identity strategy (no cross-API unique id exists). Same-name records WITHIN the canonical API are genuinely distinct devices and stay distinct (`#N` ids).
Rationale: 2026-08-25 fix — 17 raw PortAudio records represented 3 real devices (12 UI duplicates); PortAudio's own docs recommend displaying devices from one host API at a time; MME additionally truncates names at 31 chars.
Applies to: All agents, all modes, all sub-agents.
```

```
C-MIC-8
Rule: Do NOT hide or filter legitimate microphones — virtual (AudioRelay, WO Mic, VB-Cable…), USB, Bluetooth, or similarly-named devices — as a side effect of cleanup, deduplication, or "list hygiene". Disabled OS endpoints that the OS's own input UI does not offer (e.g. WDM-KS-only Line In / Stereo Mix while disabled in Windows) are out of scope for the canonical view by definition; the moment the OS offers them, the canonical enumeration includes them automatically.
Rationale: The canonical host-API view equals the OS Settings input list 1:1 — any extra filtering on top risks hiding a device the user explicitly selected (see C-MIC-2).
Applies to: All agents, all modes, all sub-agents.
```

```
C-MIC-9
Rule: Do NOT deduplicate `System Default` against the physical device it currently resolves to, and do NOT drop it from any microphone surface. System Default is a separate SELECTION SEMANTIC (follows the OS default dynamically); the physical device list is additive alongside it.
Rationale: Collapsing them would freeze the user to one device and break the OS-default-following behavior.
Applies to: All agents, all modes, all sub-agents.
```

```
C-MIC-10
Rule: Do NOT add a second microphone-enumeration path for any consumer. The Microphone page, tray submenu, onboarding, and any future surface MUST consume the one canonical `list_microphones()` model (via `app._microphones` / `tray.set_microphones`). Tray/page divergence or a consumer-local `sd.query_devices()` call reintroduces the duplicate-device class of bug.
Rationale: 2026-08-25 audit confirmed the single-source chain (list_microphones → app._microphones → {IPC get_microphones, tray.set_microphones}); the tray is a pure pass-through by design and pinned by tests.
Applies to: All agents, all modes, all sub-agents.
```

```
C-MIC-11
Rule: Do NOT render long option descriptions permanently in the Microphone Quality selector. Descriptions live behind the shared `InfoTooltip` (`components/feedback/InfoTooltip.tsx`) — one help trigger beside the section label, one per option row. Do NOT create a second tooltip primitive, do NOT duplicate the same description in multiple places, and do NOT add an info icon beside the collapsed header's current-value line.
Rationale: 2026-08-25 compaction — always-visible paragraphs tripled the component height and repeated the Auto description twice; the shared Settings tooltip pattern keeps the rows scannable.
Applies to: All agents, all modes, all sub-agents.
```

```
C-MIC-12
Rule: Do NOT style the microphone level fill with anything but the solid primary token (`bg-primary`), and do NOT put borders, inner padding, or per-level color ladders on the LevelBar fill/track pair. The track is neutral (`bg-border`), the fill is flush full-height `scaleX`-animated primary; clipping is signaled by the ⚠ glyph and aria tier text, never by recoloring the fill. The rAF loop must write ONLY the transform (never `backgroundColor`).
Rationale: 2026-08-25 fix — the accent/primary/destructive ladder plus a track border made the fill look diluted and surrounded by a layer; the duplicated color function in the hook was an E7 violation.
Applies to: All agents, all modes, all sub-agents.
```

```
C-MIC-13
Rule: Do NOT introduce redundant status labels where selection state is already communicated by the surrounding UI (e.g. a "Selected microphone" pill next to the mic name on the test card). Selection is shown by the radio list, the card context, and aria state; recording state by the Stop button + countdown + live feedback.
Rationale: 2026-08-25 removal — the badge duplicated information, added a decorative highlight, and left an awkward gap; redundant labels invite drift from the actual state.
Applies to: All agents, all modes, all sub-agents.
```

```
C-MIC-14
Rule: Do NOT regress the Microphone Quality selector's settled UI contract: (1) the header's info icon sits immediately beside the "Microphone Quality" label INSIDE the AccordionTrigger via the shared `InfoTooltip` `triggerAs="inline"` span variant (never an absolutely-positioned overlay anchored to the AccordionItem — `top-1/2` on the item slides into the option list during the expand animation; never a nested real `<button>` — invalid DOM + toggles the accordion); (2) each option row is `[title + its own info icon] … [radio far right]` — the radio stays FIRST in DOM (Radix roving tabindex/reading order) and moves visually via `order-last ms-auto`; (3) one horizontal padding system only (the shared AccordionContent px-4 == trigger px-4; rows carry no extra px); (4) option labels stay concise (Auto / Studio / Noisy Room / OFF / Advanced) with descriptions available ONLY through the tooltips; (5) the LevelBar fill keeps its rounded caps at EVERY level via the counter-scaled radius (`--level` var + `border-radius: calc(3px / max(var(--level), 0.03)) / 3px`) while the rAF loop continues to write ONLY the transform.
Rationale: 2026-08-25 polish pass — the overlay icon visually migrated into the option list mid-animation (looked like a duplicate ? beside an option), radios/labels/padding drifted from the Settings pattern, and scaleX squashed the fill's caps.
Applies to: All agents, all modes, all sub-agents.
```

```
C-MIC-15
Rule: Do NOT regress the Microphone Quality selector's COMPACT SINGLE-ROW header (refines C-MIC-14's two-line header and carves a per-instance exception out of C-MODELS-4): the collapsed state is ONE AccordionTrigger row — left group `[MICROPHONE QUALITY label + inline ? tooltip]`, right group `[active-filter chip (non-interactive span, keeps data-testid="mic-preset-current") + rotating chevron]`. The chevron is a single `ArrowDown01Icon` that rotates via `group-data-[state=open]/accordion-trigger:rotate-180` + `transition-transform duration-200` (collapsed = points down/can expand; expanded = points up/can collapse) — never a swapped glyph, never two icons. The primitive's persistent PlusSignIcon is hidden ON THIS INSTANCE ONLY via `[&_[data-slot=accordion-trigger-icon]]:hidden` (ui/accordion.tsx itself stays untouched — every other accordion keeps its `+`). The value chip is a plain span (SelectTrigger-style shell: rounded-md border-border/10 bg-background) — never a nested button. The expanded options container carries exactly ONE deliberate extra inset (`px-2` on the RadioGroup; total 24px) — no per-row padding layer. Do not reintroduce the two-line stacked header, a `+`/static icon, or edge-touching option content.
Rationale: 2026-08-25 compaction pass (user decision) — one-line header halved the collapsed height; the rotating chevron + grouped value chip match the app's SelectTrigger/disclosure language; the per-instance icon hide keeps C-MODELS-4 intact for the Models page accordions.
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
SKIPPED: <task ID or finding ID> — conflicts with AGENTS.md `Hard "Don'ts"`: <C-ID> (<one-line rule summary>)
```

The user can `grep` `worklog.md` for `SKIPPED:` to see every constraint-driven skip across sessions. This audit trail is essential for understanding why work was deferred — and for deciding whether a constraint should be relaxed in the future.

---

## Final note

This section is intentionally spare — the user fills it in over time as they discover areas where the cloud agent's "improvements" would damage the project's intent. Every rule here was added because a cloud agent (or a session in a merge) previously did the prohibited thing and the user had to revert it. Adding a rule here prevents the next agent from repeating the mistake.

---

## Review.md High Priority task admission (permanent rule - append-only)

Every task added to the `## High Priority` section of `review.md` MUST satisfy all four criteria - an entry that fails any of them must not be added as-is:

1. **Evidence-based** - grounded in the current code/behavior with concrete evidence (exact error text, file:line citations, or reproduced observations). Never invent features, problems, or fixes without repository evidence; never reuse a stale review.md claim at face value (verify against the code first).
2. **High-impact** - addresses a real user-facing or infrastructure failure/opportunity with a clearly articulated why-it-matters. No arbitrary cleanup, churn-for-churn, or low-value polish as a High Priority item.
3. **Clearly scoped** - a bounded problem/opportunity with a concrete expected outcome and (where the root cause is already known) a fix direction, so a sub-agent can execute it independently.
4. **Non-redundant** - do not duplicate an existing review.md entry, an already-fixed behavior, or another task in the same section; when a task's investigation surface overlaps another task, cross-reference instead of duplicating.

Task selection MUST favor improvements with significant product impact while minimizing unnecessary file/component overlap between independently delegated tasks (so parallel sub-agents do not conflict). When in doubt, verify before writing, and record verification. This rule is itself append-only and must never be overwritten or weakened by an agent.

---

## Category: Configuration Canonicality & Microphone Startup Reconciliation

```
C-CONF-1
Rule: Do NOT create a second source of truth for application settings. `config.json` (managed by `voice_typer/server/config`) is THE canonical store; every producer and consumer of a setting (Settings UI, Microphone page, backend commands, tray, onboarding, autostart) must read/write through the Config instance and persist via its save path. Never mirror settings into renderer-local storage as an authority, and never write config values from Electron/Tauri hosts directly.
Rationale: Parallel stores drift silently; the renderer already receives authoritative updates through get_config + config_changed pushes.
Applies to: All agents, all modes.
```

```
C-CONF-2
Rule: Do NOT leave microphone-selection validation to the Microphone page. The persisted `config.microphone` value MUST be validated/reconciled against the live device enumeration during startup (see `_reconcile_configured_microphone` in `voice_typer/server/startup_tasks.py`, invoked from `load_microphones`). The page renders already-reconciled state; its mount-time reconcile is only a mid-session hot-unplug safety net.
Rationale: Page-side-only reconciliation made stale selections survive restarts and produced noisy user-visible recovery exactly when the user opened the page.
Applies to: All agents, all modes.
```

```
C-CONF-3
Rule: Do NOT surface startup reconciliation of a stale/unavailable microphone to the user (no tray notification, no snack). Stale → System Default is SILENT for users; the diagnostic trail is one WARNING log line naming the stale id, the device count, and the recovery action (`[MIC] Configured microphone ... recovered to System Default`). A VALID configured device must be left untouched and logged healthy at INFO.
Rationale: A stale persisted id is an internal configuration inconsistency, not a user error; warning dialogs train users to dismiss real errors.
Applies to: All agents, all modes.
```

```
C-CONF-4
Rule: Do NOT change the canonical meaning of `"microphone": null`. null IS the intentional representation of "use System Default" end-to-end (validators allow str|None, resolvers/Recorder treat None as OS default, C-MIC-1 pins fresh-install default). Never replace it with a sentinel string or persist a concrete default-device id in its place. When a legacy id shape resolves to a live device, migrate it forward to the stable `"<host api>|<name>[#N]"` id instead of leaving both representations around.
Rationale: One unambiguous representation keeps config.json, backend state, UI state, and actual device selection in agreement.
Applies to: All agents, all modes.
```

```
C-CONF-5
Rule: Do NOT make runtime initialization overwrite valid persisted settings with defaults/stale values without an explicit migration/defaulting reason (corrupt-file quarantine, first-run defaults, versioned migration with .bak backup — all existing, all logged). Known accepted edge: an OLDER build loading a NEWER-schema config.json drops unknown keys at its next explicit save (warned once per process in config/loader.py `_filter_unknown_keys_impl`; newer-than-build keys are preserved until that save). Dev (`npm run dev`) and built runtimes intentionally share ONE profile dir (`~/.voice-typer` legacy-first); concurrent double-writes are prevented by the Electron single-instance lock + Python `Local\VoiceTyperSingleInstance` mutex — do not add per-runtime profile splits or second locks without a product decision.
Rationale: Distinguishes genuine bugs from the documented stale-build downgrade; protects the single-profile/single-instance architecture agents might "fix" wrongly.
Applies to: All agents, all modes.
```
```
C-MIC-16
Rule: Do NOT treat a concrete valid microphone selection as a failure state. A selected non-default device that resolves against `list_microphones()` MUST open, monitor, test, and record independently of the Windows/OS System Default device. Device-selection errors (`device_lost`, disconnect banners) must be emitted ONLY on evidence of actual unavailability (stream finished while still the CURRENT active stream, or N consecutive zero-chunks) — never as a side effect of intentional stream stop/close during device switches or page unmounts. The level-monitor's finished-callback is identity-guarded for exactly this reason; do not regress it to an unguarded callback.
Rationale: PortAudio fires PaStreamFinishedCallback on EVERY inactive transition including intentional stop()/close(); an unguarded callback made every device switch show "Selected microphone disconnected".
Applies to: All agents, all modes.
```

```
C-MIC-17
Rule: Do NOT return microphone-test WAV audio inline over IPC. Completed test WAVs (~1 MB each at device-native rates) exceed the 1 MiB single-frame IPC cap; the stop response MUST carry small file references ({"path","bytes"}) pointing at `<config>/mic-test-recordings/`, and the renderer fetches bytes via the chunked `microphone_test_read_audio` command (slices ≤256 KiB binary). Never raise `_TCP_MAX_OUTBOUND_BYTES`/`_MAX_FRAME_BYTES` to fit an oversized payload, never re-serialize whole recordings into base64 stop responses, and keep the keep-only-latest purge on `start_test_recording`.
Rationale: The dropped-frame failure mode is silent: backend logs success while the frontend times out with no result.
Applies to: All agents, all modes.
```

```
C-MIC-18
Rule: Do NOT make the microphone-test duration configurable again, and do NOT render duplicate/conflicting time displays during a test. Duration stays fixed at `MICROPHONE_TEST_DURATION_SEC = 10`; the UI shows exactly ONE timer readout (the LiveQualityFeedback elapsed/total progressing 00:00 → 00:10). The redundant voice-quality status line ("Waiting for voice…" / "✓ Voice detected" / quality-tier text) stays REMOVED — the live LevelBar owns level feedback. UI test state must derive from the real backend lifecycle (starting → recording → completed/stopped/error), never a fake recording state.
Rationale: Duplicate timers + flickering textual level states were noise; a button implying indefinite recording desyncs from the auto-stopped backend.
Applies to: All agents, all modes.
```

```
C-MIC-19
Rule: Do NOT break the post-recording result pipeline: recording completion (auto-stop OR manual stop) MUST flow through ONE canonical finalize path into the same completed-test state, and `microphone_test_read_audio` must stay a CHEAP rate-limit cost (cost 1) because a full transfer is ~8 bounded ≤256 KiB slice reads fired back-to-back against the shared per-connection burst budget. Do NOT assign heavy cost weights to chunked file-transfer commands, do NOT let the visible timer intervals be cleared by dep-driven effect cleanups on the running-state transition (unmount-only cleanup + synchronous internal lifecycle flag), and treat a backend `success:false` / "No test running" response after finalization as a benign silent no-op.
Rationale: A cost-30 weight made every completed 10s test exhaust the shared burst window mid-transfer (silently dropped tail slices → no results); an effect-cleanup killed the timer one commit after creation (frozen 00:00) while the backend kept recording.
Applies to: All agents, all modes.
```

```
C-MIC-20
Rule: Do NOT fabricate transcription-derived metrics when no speech model is available. The backend must set `transcription_unavailable` (+ reason `no_engine_loaded`) on EVERY non-transcribable path including `models == None`; the frontend must render an explicit N/A (`microphoneTest.qualityNotApplicable`) for the estimated-transcription-quality row whenever that flag is present — never a numeric score derived from absent data. Audio-derived analysis (volume/noise/clipping/voice-activity from the captured WAV) is independent of any model and must keep working; raw playback must never require a model.
Rationale: Without these gates the UI showed a false "0% Estimated Transcription Quality" for users with no model installed, misrepresenting a working microphone as a failed test.
Applies to: All agents, all modes.
```

```
C-MIC-21
Rule: Chunked file-transfer responses whose fragments are reassembled client-side by verbatim base64 concatenation MUST be sliced on 3-byte-aligned boundaries (interior fragments carry no "=" padding). Do NOT return unaligned interior slices (e.g. raw 256*1024, which % 3 == 1), do NOT send oversized audio payloads inline over capped IPC frames, and any multi-buffer byte-count logging must name each buffer explicitly (raw vs filtered) rather than emitting bare `N+N`.
Rationale: Mid-stream base64 padding silently corrupted every multi-chunk playback ("Could not play the test recording") even though both files were written correctly; the ambiguous `883330+883330` log line read as accidental duplication instead of two distinct artifacts.
Applies to: All agents, all modes.
```

---

## Category: Privacy & Background Startup

```
C-BG-1
Rule: Do NOT let persisted navigation state (vt_nav_state, last page) cause privacy-sensitive resources such as microphone live monitoring to activate while the window is hidden/background (autostart with VT_START_HIDDEN=1). Persisted restore of the Microphone page while hidden MUST NOT start the level monitor; the page may initialize live monitoring ONLY when its user-facing usage context is active (document.visibilityState === "visible" and the Microphone page is the current visible route). Background startup that restores "microphone" MUST redirect to "home" (App.tsx timed visibility grace + useMicrophoneLevelMonitor deferred start) and defer monitoring until the user intentionally navigates to Microphone while visible. The same invariant applies to any future page that would auto-initialize a privacy-sensitive or resource-intensive capability.
Rationale: Closing on Microphone persists that page; without a hidden-aware guard the next background autostart mounts MicrophonePage off-screen and immediately opens a continuous InputStream, lighting the OS mic indicator (Windows taskbar mic icon / macOS orange dot) while the user has not opened the UI. The fix is architectural (navigation + monitor visibility gates + Tauri VT_START_HIDDEN hide), not hiding the indicator or disabling mic globally. Established 2026-08-30.
Applies to: All agents, all modes, all sub-agents.
```

---

## Category: Shared Destructive Actions & Filter Consistency (2026-08-30 polish pass)

```
C-UI-9
Rule: Do NOT make any ``Clear All`` destructive button muted-on-hover or permanently tinted. Every ``Clear All`` control that wipes an entire collection (History, Vocabulary, Templates) MUST be muted at rest (``text-(--text-muted)`` + outline ``border-border/5`` via ``variant="outline" size="sm"``) and on hover become the SAME solid destructive treatment used by ``ConfirmDialog``'s ``variant="destructive"`` confirm action: ``hover:border-destructive hover:bg-destructive hover:text-destructive-foreground`` (near-white ``text-destructive-foreground`` icon + label, not ``hover:text-(--text-primary)`` which is dark in light mode and fails contrast, and not a 5% ``bg-destructive/5`` wash). The icon inherits ``currentColor`` — no separate icon color override. Keep ``gap-2`` + ``size-4`` icon + ``Delete01Icon strokeWidth 2`` for spacing/icon alignment, and preserve ``focus-visible:ring-3 ring-ring`` from the Button base.
Rationale: Vocabulary/Templates already used muted→solid-red on hover but with the wrong ``hover:text-(--text-primary)`` token (dark-on-red in light mode), while History was permanently ``border-destructive/40 text-destructive/80`` with a 5% hover wash. Standardizing to muted→solid-red + ``destructive-foreground`` makes the hover unambiguously read as the destructive wipe (the dialog's confirm button is the reference) and keeps Favorites (warning tint) visually distinct from the destructive action. Established 2026-08-30.
Applies to: All agents, all modes, all sub-agents.
```

```
C-FILTER-1
Rule: Do NOT let History, Vocabulary, and Templates use different sort/filter button visuals. The three pages MUST share the SAME ``SortSelect`` primitive (``voice_typer/client/src/renderer/src/components/common/SortSelect.tsx``): ``SelectTrigger size="sm" hideChevron`` + ``className="text-(--text-muted) transition-[color,box-shadow,background-color] hover:text-(--text-primary)"`` with ``Sorting01Icon size-4 strokeWidth 2`` inheriting ``currentColor``, and ``SelectContent position="popper" align="start" className="rounded-xl border border-border/5 bg-(--bg-subtle)"``. Dimensions (``data-[size=sm]:h-8`` via ``select.tsx`` base ``rounded-4xl border-border/5 bg-background text-sm``), border, typography, icon, spacing (``flex w-full flex-wrap gap-2``), hover/focus (``focus-visible:border-ring focus-visible:ring-3``), and interaction (hideChevron because the sort glyph already communicates the control) must stay identical — do not reintroduce History's old ``ChevronDownIcon``, default ``bg-popover`` ring, ``item-aligned`` centering, or ``ml-auto`` (use ``ms-auto`` for RTL).
Rationale: History's sort dropdown previously rendered a second chevron next to the sort glyph, used the generic popover surface, and was ``item-aligned``/``center`` (opening visibly right of short labels), while Vocabulary/Templates shared the muted + popper/start + subtle-surface pattern. Unifying on ``SortSelect`` eliminates the History-specific drift and ensures a single source of truth for dimensions/border/typography/icon/spacing/hover/focus. Established 2026-08-30.
Applies to: All agents, all modes, all sub-agents.
```

```
C-MODELS-5
Rule: Do NOT render the Models page ``no-model`` state as a centered ``EmptyState`` block. When ``config.model_size === ""`` (backend ``NO_MODEL_SIZE`` sentinel) the page MUST show a compact, dismissible banner positioned independently of the main content flow (``sticky top-0 z-10`` between the active-model summary and the tab switcher, not a centered ``flex flex-col py-16`` block that pushes cards below the fold). The banner uses the shared accent tint (``rounded-xl border border-accent/20 bg-accent/5``) with ``AiBrain03Icon text-accent`` + the precise ``C-UI-2``-compliant copy ``models.noModelBanner`` = ``"No speech model is selected. Select a model below."`` (not the vague ``models.noModelSelected`` = ``"No model selected"``), localized consistently in all 8 ``i18n/translations/*.json`` files, ``role="status" aria-live="polite"`` (``data-testid="models-no-model-banner"``), and a close ``X`` (``Cancel01Icon``) far-right with ``aria-label={t("common.close")}`` + ``hover:bg-foreground/10 hover:text-(--text-primary) focus-visible:ring-3`` that writes ``sessionStorage "models:noModelBannerDismissed"`` = ``"1"`` session-scoped; the banner reappears when a model is selected (effect clears the flag). Do NOT change the existing ``model_size === ""`` selection logic.
Rationale: The centered ``EmptyState`` consumed ~120px vertical space and duplicated the ``activeModelSummary`` banner's accent language while leaving the localized copy vague. The sticky accent banner with session-dismiss follows the ``VocabDuplicateBanner`` dismissal pattern and the ``C-UI-2`` precise-copy rule, keeps the main card flow intact, and respects the 8-locale requirement (added 2026-08-30 after user approval of Option B wording).
Applies to: All agents, all modes, all sub-agents.
```
