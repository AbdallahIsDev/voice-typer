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
Rule: Do NOT add any network call (HTTP, WebSocket, DNS, etc.) to the production code path. Voice Typer is an OFFLINE application — see ADR-0001 and the privacy docs. Even "anonymous telemetry" is forbidden. If a task requires network access for a feature, document the recommendation in `worklog.md` and SKIP the implementation.
Rationale: The offline guarantee is a core product promise. Any network call breaks it.
Applies to: All agents, all modes.
```

---

## Category: Testing & Baselines


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
