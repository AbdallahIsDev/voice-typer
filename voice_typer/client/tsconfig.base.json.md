# tsconfig.base.json — `noUncheckedIndexedAccess` Status

## Flag State

`noUncheckedIndexedAccess: true` is **ENABLED** in `tsconfig.base.json`.
This flag was enabled by upstream commit `a766c8cc` ("feat(client): add
istanbul coverage, electron upgrade, usePython hardening") on 2026-08-01,
which addressed TX-38 by enabling the flag while explicitly accepting the
~204 resulting type errors as a trade-off ("the flag catches real bugs").

## Verification Behavior

| Command                  | Exit Code | Notes                                                                                  |
| ------------------------ | --------- | -------------------------------------------------------------------------------------- |
| `npx tsc --noEmit`       | 0         | Root `tsconfig.json` uses project references with `files: []`, so plain `tsc` no-ops.  |
| `npx tsc -b --noEmit`    | 1         | Project's actual typecheck (`typecheck:root` script). Produces **186 errors** across **46 files**. |
| `npm run typecheck`      | 1         | Equivalent to `tsc -p tsconfig.web.json --noEmit && tsc -p tsconfig.node.json --noEmit`; also fails. |

## Why the 186 Errors Are NOT Fixed Here

Fixing all 186 errors requires per-site guards, assertions, or `?? defaultValue`
fallbacks across 46 files (some of which are owned by other concurrent
sub-agents: the 5 test files under
`src/renderer/src/{hooks,pages}/__tests__/` are owned by another sub-agent).
This is too large a refactor for
one Low-severity sub-agent entry. The flag itself is enabled (the goal of
TX-38), and the remaining errors will be addressed in a dedicated TS strictness
pass.

Per **Hard Rule #4 (NEVER DOWNGRADE)**, the flag is **NOT** reverted: doing so
would unwind the upstream strictness improvement made in `a766c8cc`.

## Top 5 Error Patterns (by TS error code)

| Rank | Code     | Count | Description                                                                   |
| ---- | -------- | ----- | ----------------------------------------------------------------------------- |
| 1    | TS2532   | 59    | `Object is possibly 'undefined'` — direct indexed access on `Record`/array.   |
| 2    | TS2345   | 50    | `Argument of type 'X \| undefined' is not assignable to parameter of type 'X'`. |
| 3    | TS18048  | 33    | `'<name>' is possibly 'undefined'` — narrowing required on optional chained.  |
| 4    | TS2322   | 23    | `Type 'X \| undefined' is not assignable to type 'X'` — assignment / return.  |
| 5    | TS2488   | 17    | `Type must have a '[Symbol.iterator]()'...` — spreading `Array \| undefined`. |

(Plus 4 minor: 2× TS2769, 1× TS6133, 1× TS2538.)

## Most-Affected Files (top 10 by error count)

1. `src/renderer/src/themes.ts` — 42
2. `src/renderer/src/lib/color-utils.ts` — 17
3. `src/main/__tests__/shutdown-hooks.test.ts` — 10
4. `src/renderer/src/hooks/__tests__/useSnackbar.test.tsx` — 9
5. `src/renderer/src/components/ui/__tests__/segmented-control.test.tsx` — 8
6. `src/renderer/src/pages/__tests__/Settings.test.tsx` — 6
7. `src/renderer/src/components/feedback/__tests__/ErrorBoundary.test.tsx` — 6
8. `src/renderer/src/__tests__/a11y-rewrite/Sidebar-aria-current.test.tsx` — 6
9. `src/renderer/src/__tests__/a11y-rewrite/App-a11y.test.tsx` — 6
10. `src/renderer/src/hooks/__tests__/useConnectionToasts.test.tsx` — 5

## Recommended Follow-Up (Dedicated TS Strictness Pass)

1. Start with the highest-density file (`themes.ts`, 42 errors): introduce a
   typed accessor that asserts non-undefined for known CSS custom properties.
2. Audit `color-utils.ts` (17 errors) for the same pattern.
3. Sweep test files: replace `arr[0]` patterns with `arr[0]!` or proper
   `expect(arr[0]).toBeDefined()` guards.
4. Production files (`src/main/i18n.ts`, `src/main/ipc/bubble-handlers.ts`,
   `src/main/windows/bubble/lifecycle.ts`, etc.): add explicit fallbacks
   rather than `!` assertions wherever the runtime semantics allow.
