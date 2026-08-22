/**
 * Renderer performance source-guard regression tests for the page
 * components.
 *
 * The runtime behaviour these guards protect (referential stability of
 * `useMemo` results, `React.memo` wrapping, `React.lazy` chunk
 * splitting, memoized derived data) is fully determined by the static
 * call sites in the source. A source-level check is more reliable than
 * a behavioural test that would have to instrument React internals
 * (e.g., spy on `React.createElement` to capture prop identity, or
 * count child re-renders via a mocked child) — both of which are flaky
 * under React 19's concurrent renderer and add significant test setup
 * boilerplate. The source grep also fails fast and points the reviewer
 * directly at the regression if a future refactor removes the
 * `useMemo` / `memo` / `lazy` wrapper.
 *
 * Covered invariants:
 *
 *  (a) Settings — `sectionProps` is wrapped in `useMemo` with
 *      `[config, updateConfig, updateConfigDebounced, _filter_settings]`
 *      deps so it has a stable identity across re-renders when those
 *      deps are unchanged. Previously a fresh object literal was built
 *      every render, which broke referential equality for every
 *      `{...sectionProps}` spread into a `*SettingsSection` child,
 *      causing every section component to re-render on every Settings
 *      page render. Also guards: the empty-state visibility derivation
 *      is keyed on `[settingsFilter]` (not recomputed per render), and
 *      `resetToDefaults` is a stable `useCallback` (ConfirmDialog's
 *      `onConfirm` benefits).
 *
 *  (b) Home — the status pill, mic toggle button, last-transcription
 *      preview, and recording timer are extracted into their own files
 *      under `pages/home/components/` and each is wrapped in
 *      `React.memo` (`export default memo(Name)` over a named function,
 *      preserving DevTools displayName), so they only re-render when
 *      their props change. Also guards the shared event-handler
 *      stability contract: `debouncedRefreshFromEvent` is a
 *      `useCallback` passed by reference to the `history_changed`
 *      subscription (and reused inside `transcription_final`) so those
 *      events don't churn handler identities on every render.
 *
 *  (c) Home hotkey reload — the per-`status_change` `get_config` fetch
 *      moved to the `config_changed` event. `status_change` fires on
 *      every recording → transcribing → idle transition, so a
 *      per-event config round-trip was wasted IPC; the hotkey only
 *      changes when Settings saves (`config_changed`), which is where
 *      it is now re-fetched.
 *
 *  (d) Home initial load — `initialLoading`'s initializer reads the
 *      stats/recent caches through the component-scoped ref-memoized
 *      loaders (`loadCachedStats(cachedStatsRef)` /
 *      `loadCachedRecent(cachedRecentRef)`), so repeated calls never
 *      hit localStorage more than once per mount. The pre-fix form
 *      (module-level mutable cache bindings + repeated raw reads) must
 *      stay gone.
 *
 *  (e) Route switching (router/PageSwitch.tsx) — secondary pages are
 *      `React.lazy(() => import(...))` for route-level code splitting
 *      while `Home` stays eager (default landing page), and the routed
 *      content renders inside `<Suspense>` with a real fallback
 *      component so first-time navigation shows a spinner instead of a
 *      blank frame.
 *
 *  (f) Dashboard — the derived render values (period stats, activity
 *      chart bars, correction stats) are computed inside `useMemo`s in
 *      the `useDashboardData` hook keyed on the history sample and the
 *      selected range, and the page consumes the memoized values rather
 *      than rebuilding them inline per render.
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const RENDERER_SRC = resolve(__dirname, "..");

function readSrc(relPath: string): string {
	return readFileSync(resolve(RENDERER_SRC, relPath), "utf8");
}

/**
 * Remove whole-line `// …` comments so prose mentions of an API (e.g.
 * "the previous `get_config` fetch") don't trip code-shape assertions.
 * Only lines whose first non-whitespace token is `//` are dropped —
 * inline trailing comments and string contents stay intact.
 */
function stripLineComments(src: string): string {
	return src.replace(/^[ \t]*\/\/.*$\r?\n?/gm, "");
}

/**
 * Slice out the body of a `usePythonEvent("<type>", ...)` call from the
 * given source (line comments stripped). Returns everything from the
 * call start up to (but not including) the next top-level
 * `usePythonEvent(` occurrence — good enough to assert on what a
 * specific subscription's handler does without parsing balanced parens.
 */
function sliceUsePythonEventBlock(src: string, type: string): string | null {
	const clean = stripLineComments(src);
	const startRe = new RegExp(`usePythonEvent\\(\\s*["']${type}["']\\s*,`);
	const startMatch = startRe.exec(clean);
	if (!startMatch) return null;
	const rest = clean.slice(startMatch.index + startMatch[0].length);
	const nextCall = rest.indexOf("usePythonEvent(");
	return nextCall === -1 ? rest : rest.slice(0, nextCall);
}

// ── Settings: stable sectionProps + memoized empty-state derivation ──

describe("Settings page keeps sectionProps referentially stable via useMemo", () => {
	it("wraps sectionProps in useMemo with the correct deps", () => {
		const src = readSrc("pages/Settings.tsx");

		// The sectionProps declaration must use useMemo — not be a
		// plain object literal. We match `useMemo(` (not `useMemo `)
		// to allow either formatting style.
		expect(src).toMatch(/const\s+sectionProps\s*=\s*useMemo\(/);

		// The deps array must include all four values the closure
		// reads: `config`, `updateConfig`, `updateConfigDebounced`,
		// and the filter predicate. Missing any one of these would
		// cause the memo to return a stale object when the omitted dep
		// changed — silently breaking the section components.
		//
		// We use a multiline regex so the deps array can span
		// multiple lines (Biome may wrap it).
		const depsMatch = src.match(
			/const\s+sectionProps\s*=\s*useMemo\([\s\S]*?\[([\s\S]*?)\]/,
		);
		expect(
			depsMatch,
			"sectionProps useMemo deps array not found",
		).not.toBeNull();
		const depsBody = depsMatch?.[1] ?? "";
		expect(depsBody).toContain("config");
		expect(depsBody).toContain("updateConfig");
		expect(depsBody).toContain("updateConfigDebounced");
		expect(depsBody).toContain("_filter_settings");
	});

	it("does not declare sectionProps as a plain object literal (regression guard)", () => {
		const src = readSrc("pages/Settings.tsx");
		// Before the fix, sectionProps was:
		//   const sectionProps = { config, updateConfig, ... };
		// This regex matches that pre-fix shape and must NOT match.
		const plainObjectPattern = /const\s+sectionProps\s*=\s*\{\s*config,/;
		expect(plainObjectPattern.test(src)).toBe(false);
	});

	it("derives empty-state visibility from settingsFilter via a memo keyed on [settingsFilter]", () => {
		const src = readSrc("pages/Settings.tsx");
		// The original buggy form flipped `hasAnyVisibleRow` state from
		// a dep-less effect (ran after every render). The current form
		// derives `hasAnyVisibleRow` purely via useMemo keyed on
		// `[settingsFilter]`, so memoized section children don't
		// re-render unless the query actually changes.
		const memoMatch = src.match(
			/const\s+hasAnyVisibleRow\s*=\s*useMemo\(\(\)\s*=>\s*\{[\s\S]*?\n\s*\},\s*\[([^\]]*)\]/,
		);
		expect(memoMatch, "hasAnyVisibleRow useMemo not found").not.toBeNull();
		const depsBody = memoMatch?.[1] ?? "";
		expect(depsBody).toContain("settingsFilter");
	});

	it("wraps resetToDefaults in useCallback", () => {
		const src = readSrc("pages/Settings.tsx");
		// resetToDefaults must be wrapped in useCallback so its identity
		// is stable across renders (ConfirmDialog's onConfirm prop
		// benefits).
		expect(src).toMatch(/const\s+resetToDefaults\s*=\s*useCallback\(/);
		// The deps array must include the values the closure reads.
		const cbMatch = src.match(
			/const\s+resetToDefaults\s*=\s*useCallback\([\s\S]*?\}\s*,\s*\[([\s\S]*?)\]\)/,
		);
		expect(
			cbMatch,
			"resetToDefaults useCallback deps not found",
		).not.toBeNull();
		const depsBody = cbMatch?.[1] ?? "";
		expect(depsBody).toContain("config");
		expect(depsBody).toContain("call");
		expect(depsBody).toContain("updateConfig");
		expect(depsBody).toContain("showSnack");
	});
});

// ── Home: memoized subcomponents + stable shared handlers ────────────

describe("Home subcomponents are memoized extracted components", () => {
	// Each status surface lives in its own file and defaults to a
	// `memo(...)` wrapper over a NAMED function export (the named
	// function preserves the displayName for React DevTools). All props
	// are primitives or stable callbacks, so referential equality holds
	// across Home's frequent state flips.
	const MEMO_COMPONENTS = [
		{ name: "RecordingStatusPill" },
		{ name: "MicToggleButton" },
		{ name: "LastTranscriptionPreview" },
		{ name: "RecordingTimer" },
	] as const;

	it.each(MEMO_COMPONENTS.map((c) => c.name))(
		"%s imports memo from react",
		(name: string) => {
			const src = readSrc(`pages/home/components/${name}.tsx`);
			// Without the react import the `memo(...)` call below would
			// be a ReferenceError.
			expect(src).toMatch(/import\s+\{[^}]*\bmemo\b[^}]*\}\s+from\s+"react"/);
		},
	);

	it.each(MEMO_COMPONENTS.map((c) => c.name))(
		"%s declares a named function and exports it wrapped in memo()",
		(name: string) => {
			const src = readSrc(`pages/home/components/${name}.tsx`);
			// Match `export function <Name>(` (the named-function form,
			// rather than an anonymous arrow) AND the
			// `export default memo(<Name>);` wrapper at the bottom of the
			// file. Dropping the memo wrapper reintroduces the
			// re-render-on-every-parent-render regression; dropping the
			// named function breaks DevTools display names.
			expect(src).toMatch(new RegExp(`export\\s+function\\s+${name}\\s*\\(`));
			expect(
				src,
				`Expected ${name}.tsx to end with export default memo(${name});`,
			).toMatch(new RegExp(`export\\s+default\\s+memo\\(\\s*${name}\\s*\\)`));
		},
	);

	it("Home composes the extracted memoized components (no inline copies)", () => {
		const src = readSrc("pages/Home.tsx");
		for (const { name } of MEMO_COMPONENTS) {
			expect(
				src,
				`Expected Home.tsx to import ${name} from ./home/components/${name}`,
			).toMatch(
				new RegExp(
					`import\\s+\\{\\s*${name}\\s*\\}\\s+from\\s+"./home/components/${name}"`,
				),
			);
		}
		// And none of them may be re-declared inline in Home.tsx.
		for (const { name } of MEMO_COMPONENTS) {
			expect(src).not.toMatch(new RegExp(`function\\s+${name}\\b`));
		}
	});

	it("shares one stable debouncedRefreshFromEvent callback across the refresh events", () => {
		const src = readSrc("pages/Home.tsx");
		// The shared refresh routine must be a useCallback (stable
		// identity across re-renders) and must be passed BY REFERENCE to
		// the history_changed subscription; transcription_final invokes
		// it from within its handler. Inline arrows at both sites would
		// defeat the identity-stability contract the dispatcher relies
		// on for skipping redundant refresh scheduling.
		expect(src).toMatch(
			/const\s+debouncedRefreshFromEvent\s*=\s*useCallback\(/,
		);
		expect(src).toMatch(
			/usePythonEvent\(\s*["']history_changed["']\s*,\s*debouncedRefreshFromEvent\s*\)/,
		);
		const block = sliceUsePythonEventBlock(src, "transcription_final");
		expect(block, "transcription_final handler not found").not.toBeNull();
		expect(block).toContain("debouncedRefreshFromEvent()");
	});
});

// ── Home: hotkey reload rides on config_changed, not status_change ───

describe("Home reloads the hotkey from config_changed instead of status_change", () => {
	it("does not fetch get_config from the status_change handler", () => {
		const src = readSrc("pages/Home.tsx");
		// status_change fires on every recording → transcribing → idle
		// transition; a per-event `get_config` round-trip was wasted
		// IPC. The handler body must not call get_config.
		const block = sliceUsePythonEventBlock(src, "status_change");
		expect(block, "status_change handler not found").not.toBeNull();
		expect(
			block,
			"status_change handler must NOT call get_config (hotkey reload belongs to config_changed)",
		).not.toMatch(/get_config/);
	});

	it("re-fetches the hotkey from the config_changed handler", () => {
		const src = readSrc("pages/Home.tsx");
		// config_changed fires when Settings saves a new config — that
		// is the only moment the hotkey can change, so this handler
		// performs the get_config fallback fetch and updates the
		// rendered hotkey via setHotkey(...).
		const block = sliceUsePythonEventBlock(src, "config_changed");
		expect(block, "config_changed handler not found").not.toBeNull();
		expect(block).toMatch(/setHotkey\(/);
		expect(block).toMatch(/get_config/);
	});
});

// ── Home: initialLoading avoids redundant localStorage reads ─────────

describe("Home initialLoading initializer dedupes localStorage reads through ref-cached loaders", () => {
	it("initializes initialLoading from the ref-backed cached loaders", () => {
		const src = stripLineComments(readSrc("pages/Home.tsx"));
		// The lazy useState initializer consults BOTH caches. Each call
		// routes through the component-scoped refs (cachedStatsRef /
		// cachedRecentRef), so the second read of the same cache is
		// served from memory instead of localStorage.
		expect(src).toMatch(
			/loadCachedStats\(\s*cachedStatsRef\s*\)\s*===\s*null\s*&&\s*loadCachedRecent\(\s*cachedRecentRef\s*\)\.length\s*===\s*0/,
		);
		// The pre-fix module-level mutable bindings must stay gone (they
		// leaked across HMR / test re-mounts and were not React-aware).
		// Anchored to line start so prose comments mentioning the old
		// names don't trip the guard.
		expect(src).not.toMatch(/^[ \t]*let\s+_cached(?:Stats|Recent)\b/m);
	});

	it("cache helpers short-circuit on a populated ref before touching localStorage", () => {
		const src = readSrc("pages/home/lib/cache.ts");
		// Stats cache: non-null ref ⇒ return without reading storage.
		expect(src).toMatch(
			/if\s*\(\s*ref\.current\s*!==\s*null\s*\)\s*return\s+ref\.current;/,
		);
		// Recent cache: populated ref ⇒ return without reading storage.
		expect(src).toMatch(
			/if\s*\(\s*ref\.current\.length\s*>\s*0\s*\)\s*return\s+ref\.current;/,
		);
	});
});

// ── Route switching: code splitting with a Suspense fallback ─────────

describe("Route switching lazy-loads secondary pages behind a Suspense fallback", () => {
	// The lazy-route table + Suspense wrapper live in the extracted
	// router/PageSwitch.tsx (App.tsx composes it).
	const ROUTE_SRC_PATH = "router/PageSwitch.tsx";

	it("imports lazy and Suspense from react", () => {
		const src = readSrc(ROUTE_SRC_PATH);
		expect(src).toMatch(/import\s+\{[^}]*\blazy\b[^}]*\}\s+from\s+"react"/);
		expect(src).toMatch(/import\s+\{[^}]*\bSuspense\b[^}]*\}\s+from\s+"react"/);
	});

	it("keeps Home as an eager (static) import — default landing page", () => {
		const src = readSrc(ROUTE_SRC_PATH);
		// Home is the default landing page; it must NOT be lazy
		// (loading it via React.lazy would add a Suspense fallback
		// flash on every app launch).
		expect(src).toMatch(/import\s+Home\s+from\s+["']@\/pages\/Home["']/);
		// And Home must NOT be wrapped in lazy(() => import(...)).
		const homeLazyPattern =
			/const\s+Home\s*=\s*lazy\(\s*\(\)\s*=>\s*import\(["']@\/pages\/Home["']\)\s*\)/;
		expect(
			homeLazyPattern.test(src),
			"Home must NOT be wrapped in React.lazy (it's the default landing page)",
		).toBe(false);
	});

	it.each([
		["AboutPage", "@/pages/About"],
		["PrivacyPage", "@/pages/Privacy"],
		["DashboardPage", "@/pages/Dashboard"],
		["HistoryPage", "@/pages/History"],
		["MicrophonePage", "@/pages/Microphone"],
		["ModelsPage", "@/pages/Models"],
		["OnboardingPage", "@/pages/Onboarding"],
		["SettingsPage", "@/pages/Settings"],
		["TemplatesPage", "@/pages/Templates"],
		["VocabularyPage", "@/pages/Vocabulary"],
	] as const)(
		"converts %s to React.lazy(() => import(%s))",
		(varName: string, importPath: string) => {
			const src = readSrc(ROUTE_SRC_PATH);
			// Escape the import path for use in a regex (paths with
			// slashes are safe in JS regex but be defensive).
			const escaped = importPath.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
			const pattern = new RegExp(
				`const\\s+${varName}\\s*=\\s*lazy\\(\\s*\\(\\)\\s*=>\\s*import\\(["']${escaped}["']\\)\\s*\\)`,
			);
			expect(
				pattern.test(src),
				`Expected: const ${varName} = lazy(() => import("${importPath}"))`,
			).toBe(true);
		},
	);

	it("wraps the routed content in <Suspense> with a real component fallback", () => {
		const src = readSrc(ROUTE_SRC_PATH);
		// The Suspense wrapper's fallback must be a dedicated fallback
		// COMPONENT (`<RouteSuspenseFallback />` today) — not null,
		// undefined, or a blank fragment, which would flash an empty
		// frame on first navigation to a not-yet-loaded chunk.
		expect(src).toMatch(
			/<Suspense\s+fallback=\{\s*<[A-Z]\w*(?:\s*\/)?\s*>\s*\}\s*>/,
		);
		// And the fallback referenced must actually be declared in the
		// same module.
		expect(src).toMatch(/function\s+RouteSuspenseFallback\s*\(/);
	});

	it("does not statically import the secondary page modules (regression guard)", () => {
		const src = readSrc(ROUTE_SRC_PATH);
		// Before the fix, all 10 pages were statically imported. After
		// the fix, only Home is static. The 9 secondary pages must
		// NOT appear in a plain `import X from "@/pages/Y"` statement.
		const secondaryPages = [
			["AboutPage", "@/pages/About"],
			["PrivacyPage", "@/pages/Privacy"],
			["DashboardPage", "@/pages/Dashboard"],
			["HistoryPage", "@/pages/History"],
			["MicrophonePage", "@/pages/Microphone"],
			["ModelsPage", "@/pages/Models"],
			["OnboardingPage", "@/pages/Onboarding"],
			["SettingsPage", "@/pages/Settings"],
			["TemplatesPage", "@/pages/Templates"],
			["VocabularyPage", "@/pages/Vocabulary"],
		] as const;
		for (const [varName, importPath] of secondaryPages) {
			const staticPattern = new RegExp(
				`import\\s+${varName}\\s+from\\s+["']${importPath.replace(
					/[.*+?^${}()|[\]\\]/g,
					"\\$&",
				)}["']`,
			);
			expect(
				staticPattern.test(src),
				`${varName} must NOT be statically imported (should be React.lazy)`,
			).toBe(false);
		}
	});
});

// ── Dashboard: derived values memoized inside useDashboardData ───────

describe("Dashboard derived stats are memoized in useDashboardData", () => {
	it("hook imports useMemo from react", () => {
		const src = readSrc("pages/dashboard/hooks/useDashboardData.ts");
		expect(src).toMatch(/import\s+\{[^}]*\buseMemo\b[^}]*\}\s+from\s+"react"/);
	});

	it("memoizes period stats, activity bars, and correction stats off the sample + range", () => {
		const src = readSrc("pages/dashboard/hooks/useDashboardData.ts");
		// Each derived value must be computed inside useMemo so unrelated
		// re-renders (refreshing flag, ago label ticks) don't rebuild the
		// stat objects / chart-bar arrays.
		for (const name of ["period", "activity", "correctionStats"]) {
			const memoMatch = src.match(
				new RegExp(
					`const\\s+${name}\\s*=\\s*useMemo\\(\\s*\\(\\)\\s*=>\\s*(\\w+)\\([^)]*\\)\\s*,\\s*\\[([^\\]]*)\\]`,
				),
			);
			expect(
				memoMatch,
				`Expected const ${name} = useMemo(() => computeXxx(...), [...]) in useDashboardData`,
			).not.toBeNull();
			// Deps must include the range so a period toggle invalidates
			// the memo.
			expect(memoMatch?.[2] ?? "").toContain("range");
		}
		// The activity bars come from buildActivityBars (single source:
		// the history sample), not an inline .map in render.
		expect(src).toMatch(
			/const\s+activity\s*=\s*useMemo\(\s*\(\)\s*=>\s*buildActivityBars\(sample,\s*range\)/,
		);
	});

	it("Dashboard consumes the memoized hook values instead of recomputing inline", () => {
		const pageSrc = readSrc("pages/Dashboard.tsx");
		// The page destructures the memoized values from the hook…
		expect(pageSrc).toMatch(
			/const\s*\{[\s\S]*?\bperiod\b,[\s\S]*?\bactivity\b,[\s\S]*?\bcorrectionStats\b,[\s\S]*?\}\s*=\s*useDashboardData/,
		);
		// …and passes the memoized activity array into the chart.
		expect(pageSrc).toMatch(
			/<ActivityChart\s+range=\{range\}\s+activity=\{activity\}\s*\/>/,
		);
		// No inline dailyActivity mapping may remain in the page render.
		expect(pageSrc).not.toMatch(/dailyActivity\s*\.\s*map/);
	});
});
