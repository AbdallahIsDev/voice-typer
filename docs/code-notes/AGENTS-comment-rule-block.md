# AGENTS.md rule block — comment discipline (C-COMMENT-1..N)

> **INSTALLED** into `AGENTS.md` (Hard "Don'ts" → `## Category: Comment Discipline`) at user request.
> Keep this file as the source snapshot; AGENTS.md is the live copy.

```
## Category: Comment Discipline

C-COMMENT-1
Rule: Do NOT let inline comments exceed ~10% of lines in any top-level code directory (voice_typer/server, voice_typer/client/src, src-tauri/src, scripts, .github/workflows). Measure with `python scripts/comment_ratio_metrics.py`. Self-explanatory code gets NO comment.
Rationale: Before the 2026 cleanup, voice_typer/server was ~53% comment lines; narrative AI fluff and task-ID headers buried real contracts. Volume is a maintenance cost, not documentation.
Applies to: All agents, all modes, all sub-agents.

C-COMMENT-2
Rule: Do NOT write multi-paragraph inline comments on complex logic. Surviving comments on interwoven/non-obvious code are limited to 1-3 lines and must state WHY (invariant, trap, non-obvious contract), never WHAT the code already says.
Rationale: Long inline essays go stale and hide the one-line contract that actually matters.
Applies to: All agents, all modes, all sub-agents.

C-COMMENT-3
Rule: Do NOT put task/session/ticket IDs (CR-xx, MIG-xx, VP-xx, XS-xx, session prefixes) in source comments, function names, or file names as the comment's content. Session prefixes belong only in review.md / worklog.md / SUMMARY.md (see C-STYLE-1).
Rationale: Task IDs are transient; code named after them becomes noise.
Applies to: All agents, all modes, all sub-agents.

C-COMMENT-4
Rule: Do NOT write AI fluff, filler adjectives, or narrative history in code comments ("elegantly", "this function beautifully...", "previously we...", "extracted from the old monolith", "before the migration...").
Rationale: History belongs in git/docs; inline history contradicts current code within weeks.
Applies to: All agents, all modes, all sub-agents.

C-COMMENT-5
Rule: Do NOT leave a comment that restates the code (`i += 1  # increment i`) or that contradicts current behavior. On every edit of a file, verify surviving comments against the code; stale comments must be fixed to match or deleted.
Rationale: Stale comments are worse than no comments — they actively mislead (C-REVIEW / E10 apply the same verification duty to comments).
Applies to: All agents, all modes, all sub-agents.

C-COMMENT-6
Rule: Do NOT bury deep lasting explanations only in inline comments. Multi-paragraph rationales, event catalogues, and architecture essays must live in `docs/code-notes/<area>.md` (or the closest existing doc). The call site keeps at most a 1-line pointer: `// NOTE: see docs/code-notes/<area>.md#anchor`. Every pointer target must exist.
Rationale: Inline essays are unreadable at the call site and unsearchable as docs; relocation preserves knowledge without bloating code.
Applies to: All agents, all modes, all sub-agents.

C-COMMENT-7
Rule: Do NOT delete or weaken protected anchors when shortening comments: SEC-*/RACE-*/PERF-* tags with their one-line why; C-* rule anchors at the exact code site they govern (especially C-CI-*, C-TAURI-*, C-WS-*, C-TOKIO-*, C-LOG-*, C-ARCH-*, C-BRAND-1, C-TEST-5, C-MIC-*, C-UI-*, C-SIDEBAR-*, C-CONF-*, C-BG-1, C-DATA-1); workflow evidence tags (NU-105, IPD-1, S1-CR-99, S4-CR-25, S10-CC-1, CRIT-7, TX-*, WR-*, XS-*, MIG-*, ZR-*); required linter/type directives (# noqa, type: ignore, eslint-disable, #[allow]) and their short rationale. Long rationales may slim: tag + 1-line why stays inline; essay moves to docs/ with a pointer.
Rationale: These tags are greppable signposts wired into AGENTS.md Hard Don'ts, CI evidence, and parity tests. Removing the anchor breaks the constraint system.
Applies to: All agents, all modes, all sub-agents.

C-COMMENT-8
Rule: Do NOT add new comments that fill space, document the obvious, or restate function names/types. When introducing a new non-obvious invariant/trap/contract, one short WHY comment is allowed; everything else is documentation work, not a code comment.
Rationale: Anti-bloat cuts both ways — new code must not re-seed the disease.
Applies to: All agents, all modes, all sub-agents.

C-COMMENT-9
Rule: Do NOT ship comment-only edits without verifying comment-pinned tests still pass (e.g. event_bus.__doc__ catalogue names + `Total: N events`, branding APP_NAME parsers, workflow YAML string pins, Rust source greps for std::thread/block_on/ALLOWED_EVENT_TYPES). If a test parses a comment/docstring, either keep the pinned tokens or update the test in the same change — never silently drop them.
Rationale: Several "docs" in this repo are actually contracts enforced by tests; relocating them without updating pins fails CI.
Applies to: All agents, all modes, all sub-agents.
```

## Supporting tool

`scripts/comment_ratio_metrics.py` — prints per-directory comment ratios and densest files. Run before/after any comment-heavy change.
