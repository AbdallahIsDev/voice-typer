/**
 * Shared harness for the page-level infinite render loop guard tests.
 *
 * Every data-fetching page (`pages/__tests__/*-render-loop-guard.test.tsx`)
 * used to carry ~180 lines of near-identical boilerplate: the unstable
 * `usePython` mock, the standard module mocks (useSnackbar / hugeicons /
 * sonner / next-themes / useNavigation), the `<Profiler>` wrapper, the
 * waitFor-settle dance, the once-per-command counter assertions, and the
 * commit bound. This module owns all of it — a page guard is now a call
 * to `renderLoopGuard({ id, page, commands, settle })`.
 *
 * The bug class under guard: a test mock (or future code) that hands out
 * a FRESH `call` identity on every render re-fires any effect listing
 * `call` in its deps — each run re-fetches and stores fresh state →
 * render → new `call` → … → unbounded render loop until the worker heap
 * is exhausted (the axe-core scan OOM, FATAL heap, whole suite died at
 * ~356 files). The harness drives the page with that worst-case mock
 * shape and asserts the page still settles: the mount load fires
 * EXACTLY `expected` times per command, and the committed render count
 * stays bounded. If future code re-introduces an unstable value into an
 * effect dep, the load re-fires and/or the render count explodes and
 * this fails fast — instead of the worker OOMing.
 *
 * Mock-install mechanics (verified empirically, see git history of this
 * file): vitest re-evaluates `vi.mock` factories in the IMPORTING test
 * file's scope, so a factory can never close over this module's
 * bindings. Every factory below is therefore fully self-contained — it
 * dynamic-imports this module and calls an exported factory, which reads
 * the module-level `activeCommands` / `activeCounters` state that
 * `renderLoopGuard`'s `beforeEach` sets for the current test run.
 */

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { Profiler } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TooltipProvider } from "@/components/ui/tooltip";

export interface GuardCommand {
	/** IPC command name, e.g. "get_config". */
	name: string;
	/** Value the mocked bridge returns for this command. */
	response: unknown;
	/** Expected call count after the page settles (default 1; 0 asserts
	 *  a command must NOT fire, e.g. a one-time migration guard). */
	expected?: number;
}

export interface RenderLoopGuardOptions {
	/** Profiler id + describe label, e.g. "home". */
	id: string;
	/** Dynamic import of the page module (must stay `() => import(...)`
	 *  so `vi.resetModules()` in beforeEach picks up a fresh module). */
	page: () => Promise<{ default: React.ComponentType<object> }>;
	/** Extra props passed to the page (e.g. Onboarding's onComplete). */
	props?: Record<string, unknown>;
	/** IPC command table — the only page-specific contract besides
	 *  `settle`. */
	commands: GuardCommand[];
	/** Assert the page settled. Called in waitFor and again after the
	 *  150ms settle window; return false while the page is still
	 *  loading. */
	settle: (s: typeof screen) => boolean;
	/** Committed-render bound (default 20; the vocabulary page settles
	 *  at ~6 and pins 15). */
	maxCommits?: number;
}

// ── Module state read by the mock factories (set per test run) ────────
let activeCommands: GuardCommand[] = [];
let activeCounters: Record<string, number> = {};

// ── Mock registrations ────────────────────────────────────────────────
// See the header note: factories must be self-contained (dynamic import
// + exported factory reading the module state above).
vi.mock("@/hooks/usePython", async () => {
	const m = await import("@/__tests__/helpers/renderLoopGuard");
	return m.unstableUsePythonMock();
});

vi.mock("@/hooks/useSnackbar", async () => {
	const m = await import("@/__tests__/helpers/renderLoopGuard");
	return m.snackbarMock();
});

vi.mock("@/hooks/useNavigation", async () => {
	const m = await import("@/__tests__/helpers/renderLoopGuard");
	return m.navigationMock();
});

vi.mock("@hugeicons/react", async () => {
	const m = await import("@/__tests__/helpers/renderLoopGuard");
	return m.hugeiconsReactMock();
});

vi.mock("@hugeicons/core-free-icons", async () => {
	const m = await import("@/__tests__/helpers/renderLoopGuard");
	return m.hugeiconsCoreMock();
});

vi.mock("sonner", async () => {
	const m = await import("@/__tests__/helpers/renderLoopGuard");
	return m.sonnerMock();
});

vi.mock("next-themes", async () => {
	const m = await import("@/__tests__/helpers/renderLoopGuard");
	return m.nextThemesMock();
});

/** The usePython mock with a DELIBERATELY UNSTABLE `call` — a fresh
 *  identity on every render (the axe-core mock's old shape). The page
 *  must settle anyway: hooks hold `call` behind a ref. `activeCounters`
 *  tracks the real IPC volume regardless of which wrapper instance the
 *  page happens to be holding. */
export function unstableUsePythonMock() {
	return {
		usePython: () => ({
			call: vi.fn(async (cmd: unknown) => {
				const name =
					typeof cmd === "string"
						? cmd
						: ((cmd as { type?: string })?.type ?? "");
				if (name in activeCounters)
					activeCounters[name] = (activeCounters[name] ?? 0) + 1;
				const def = activeCommands.find((c) => c.name === name);
				return def ? def.response : {};
			}),
			pythonPort: 9999,
		}),
		usePythonEvent: vi.fn(),
	};
}

export function snackbarMock() {
	return {
		useSnackbar: () => ({ showSnack: vi.fn() }),
		showUndoableToast: vi.fn(),
	};
}

export function navigationMock() {
	return {
		useNavigation: () => ({
			navigate: vi.fn(),
			pendingConsentField: null,
			consumeConsentField: vi.fn<() => string | null>(() => null),
		}),
	};
}

export function hugeiconsReactMock() {
	return {
		HugeiconsIcon: ({
			children,
			icon,
		}: {
			children?: React.ReactNode;
			icon?: { name?: string };
		}) => (
			<span data-testid="hugeicon" data-name={icon?.name}>
				{children}
			</span>
		),
	};
}

export async function hugeiconsCoreMock() {
	const { createHugeiconsMock } = await import(
		"@/__tests__/helpers/hugeicons-mock"
	);
	return createHugeiconsMock();
}

export function sonnerMock() {
	return {
		toast: {
			success: vi.fn(),
			error: vi.fn(),
			warning: vi.fn(),
			info: vi.fn(),
			dismiss: vi.fn(),
		},
		Toaster: () => null,
	};
}

export function nextThemesMock() {
	return { useTheme: () => ({ theme: "light" as const }) };
}

/** Define the standard render-loop guard suite for one page. Call once
 *  at the top level of the page's `*-render-loop-guard.test.tsx` file. */
export function renderLoopGuard(opts: RenderLoopGuardOptions) {
	const maxCommits = opts.maxCommits ?? 20;

	describe(`${opts.id} page — render-loop guard`, () => {
		beforeEach(() => {
			activeCommands = opts.commands;
			activeCounters = {};
			for (const c of opts.commands) activeCounters[c.name] = 0;
			localStorage.clear();
			// NOTE: do NOT call vi.resetModules() here. The mock factories
			// above dynamic-import THIS module to read the command table /
			// counters (vitest re-evaluates factory bodies in the test
			// file's scope, so they cannot close over this module's
			// bindings). resetModules would wipe this module from the
			// cache, and the factory's next dynamic import would evaluate
			// a FRESH copy with empty state — counters never increment and
			// every command returns {}. Per-file isolation (isolate: true)
			// already guarantees a fresh module registry per test file,
			// and these suites are one test per file, so no reset is
			// needed for cross-test leakage.
		});

		afterEach(() => {
			cleanup();
		});

		// Hard per-test timeout so a regressed loop fails the TEST (and
		// the suite) instead of spinning the worker into a heap OOM.
		it("settles with a per-render-unstable call mock: one load, bounded renders", {
			timeout: 5000,
		}, async () => {
			let commits = 0;
			const mod = await opts.page();
			const Page = mod.default;
			render(
				<TooltipProvider delayDuration={200}>
					<Profiler
						id={opts.id}
						onRender={() => {
							commits += 1;
						}}
					>
						<Page {...(opts.props ?? {})} />
					</Profiler>
				</TooltipProvider>,
			);

			// The page settles (per-page predicate) — the empty state
			// / heading / button replaces the loading spinner once
			// the initial load lands.
			await waitFor(
				() => {
					if (!opts.settle(screen)) {
						throw new Error(`${opts.id} page did not settle`);
					}
				},
				{ timeout: 3000 },
			);
			// Let any trailing effects / microtasks settle, then
			// confirm the tree is still stable — an effect loop would
			// keep mutating the DOM (and inflating `commits`).
			await new Promise((resolve) => setTimeout(resolve, 150));
			if (!opts.settle(screen)) {
				throw new Error(`${opts.id} page unstable after settle window`);
			}

			// The mount load fired EXACTLY `expected` times per
			// command — no re-fetch loop.
			for (const c of opts.commands) {
				expect(activeCounters[c.name] ?? 0).toBe(c.expected ?? 1);
			}

			// Committed renders are bounded. A settled page mounts +
			// settles in a handful of commits; an unbounded re-render
			// loop blows well past this bound (and would already have
			// timed out above).
			expect(commits).toBeLessThanOrEqual(maxCommits);
		});
	});
}
