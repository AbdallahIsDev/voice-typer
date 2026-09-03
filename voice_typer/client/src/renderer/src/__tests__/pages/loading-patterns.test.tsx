/**
 * Loading-patterns regression suite.
 *
 * Verifies that the owned pages + the Spinner component apply the
 * standardized loading pattern:
 *
 *   1. ``Spinner`` accepts an optional ``label`` prop that renders a
 *      visible, contextual loading message (e.g. "Loading microphones…")
 *      AND uses it as the accessible name (overriding the generic
 *      ``a11y.loading`` fallback). ``Spinner`` is reserved for INLINE
 *      action-progress indicators (e.g. the History "Load More" button)
 *      — page-content loading uses skeletons.
 *   2. The 5 first-load-only pages (History / Microphone / Templates /
 *      Vocabulary / Models) mount into a page-shaped Skeleton
 *      composition (``components/feedback/skeletons.tsx``): an
 *      ``<output aria-busy="true">`` region whose accessible name is
 *      the generic ``a11y.loading`` key (verified behaviorally by
 *      mounting each page in its first-load state, not by scanning
 *      source text).
 *   3. The ``X.loading`` i18n keys exist in ALL 8 locale files.
 *      ``history.loading`` labels the History load-more button; the
 *      remaining keys are retained in the catalogues (unused by pages
 *      since the skeleton migration) so locales stay complete.
 *
 * Tests run on LINUX (sandbox). No backend / IPC required — the page
 * mounts are driven by the shared stable-mocks harness with a
 * never-resolving ``call`` so the first-load state persists for the
 * assertion.
 */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ar from "@/i18n/translations/ar.json";
import de from "@/i18n/translations/de.json";
import en from "@/i18n/translations/en.json";
import es from "@/i18n/translations/es.json";
import fr from "@/i18n/translations/fr.json";
import hi from "@/i18n/translations/hi.json";
import ru from "@/i18n/translations/ru.json";
import zh from "@/i18n/translations/zh.json";

// Stub `t()` so the Spinner's aria-label / visible label resolves to a
// sentinel string we can assert on. Use `importOriginal` to preserve the
// rest of the i18n module surface (the pages pull `getLocale` / `useT` /
// `tChoice` from the same module).
vi.mock("@/i18n/i18n", async (importOriginal) => {
	const actual = await importOriginal<typeof import("@/i18n/i18n")>();
	return {
		...actual,
		t: (key: string) => `[t]${key}`,
	};
});

// Shared stable-mocks preamble (see helpers/stableMocks.tsx): the
// assertable singletons + one vi.mock line per module. Every data page
// below mounts through these — identical to the per-page suites.
import {
	hugeiconsCoreMock,
	hugeiconsReactMock,
	lastUpdatedMock,
	navigationMock,
	nextThemesMock,
	pythonMock,
	resetStableMocks,
	snackbarMock,
	sonnerMock,
	stableMocks,
} from "@/__tests__/helpers/stableMocks";

vi.mock("@/hooks/usePython", () => pythonMock({ noopEvent: true }));
vi.mock("@/hooks/useSnackbar", () => snackbarMock());
vi.mock("@/hooks/useLastUpdated", () => lastUpdatedMock({ withRefresh: true }));
vi.mock("@/hooks/useNavigation", () => navigationMock());
vi.mock("@hugeicons/react", () => hugeiconsReactMock());
vi.mock("@hugeicons/core-free-icons", () => hugeiconsCoreMock());
vi.mock("sonner", () => sonnerMock());
vi.mock("next-themes", () => nextThemesMock());

import { Spinner } from "@/components/feedback/Spinner";

const LOCALES: Record<string, typeof en> = {
	en,
	ar,
	de,
	es,
	fr,
	hi,
	ru,
	zh,
};

const LOADING_KEYS = [
	"microphone.loading",
	"templates.loading",
	"vocabulary.loading",
	"history.loading",
] as const;
// `models.loading` was REMOVED from the catalogue — the Models page
// renders a page-shaped ModelsSkeleton (no Spinner label), so the key
// was dead surface (deleted across all 8 locales with the dead-key
// cleanup).

// Resolve a dot-separated key path against a nested JSON object.
// Returns true if the path exists.
function hasKey(obj: unknown, dottedKey: string): boolean {
	const parts = dottedKey.split(".");
	let cur: unknown = obj;
	for (const p of parts) {
		if (cur && typeof cur === "object" && p in (cur as object)) {
			cur = (cur as Record<string, unknown>)[p];
		} else {
			return false;
		}
	}
	return typeof cur === "string";
}

describe("Spinner labeled variant", () => {
	afterEach(() => {
		cleanup();
	});

	beforeEach(() => {
		vi.clearAllMocks();
	});

	it("renders the visible label text when label is provided", () => {
		render(<Spinner label="[t]microphone.loading" />);
		// The label text appears visibly next to the glyph (the glyph
		// itself is aria-hidden decoration; the label is real text).
		expect(screen.getByText("[t]microphone.loading")).toBeInTheDocument();
	});

	it("uses the label as the accessible name (aria-label) when provided", () => {
		render(<Spinner label="[t]templates.loading" />);
		// The wrapper <span role="img"> carries the contextual aria-label.
		const img = document.querySelector('span[role="img"]');
		expect(img).not.toBeNull();
		expect(img?.getAttribute("aria-label")).toBe("[t]templates.loading");
	});

	it("falls back to the generic a11y.loading aria-label when `label` is absent", () => {
		render(<Spinner />);
		const img = document.querySelector('span[role="img"]');
		expect(img).not.toBeNull();
		// a11y.loading is the i18n key — the stub returns [t]<key>.
		expect(img?.getAttribute("aria-label")).toBe("[t]a11y.loading");
		// No visible label text without an explicit label prop.
		expect(screen.queryByText("[t]a11y.loading")).toBeNull();
	});

	it("renders a plain aria-hidden <div> (no label) when decorative=true", () => {
		// `decorative` ignores `label` — the parent already supplies
		// the accessible name, so an additional visible label would be
		// redundant.
		render(<Spinner label="[t]should-be-ignored" decorative />);
		expect(screen.queryByText("[t]should-be-ignored")).toBeNull();
		// No role="img" region (the decorative variant renders an
		// aria-hidden <div> instead).
		expect(document.querySelector('span[role="img"]')).toBeNull();
	});
});

describe("first-load-only pages mount into a page-shaped Skeleton", () => {
	// Each case dynamically imports the page so vi.mock registrations
	// above apply, then mounts it in its FIRST-LOAD state: every data
	// hook starts ``loading === true`` synchronously and the shared
	// ``call`` mock below never resolves, so the page-shaped skeleton
	// composition stays on screen for the assertion. The skeleton
	// renders as an <output> region (implicit role "status") with
	// aria-busy="true" and the localized a11y.loading accessible name.
	const PAGE_CASES: Array<{
		name: string;
		load: () => Promise<{ default: React.ComponentType<object> }>;
	}> = [
		{
			name: "History",
			load: () => import("@/pages/History"),
		},
		{
			name: "Microphone",
			load: () => import("@/pages/Microphone"),
		},
		{
			name: "Templates",
			load: () => import("@/pages/Templates"),
		},
		{
			name: "Vocabulary",
			load: () => import("@/pages/Vocabulary"),
		},
		{
			name: "Models",
			load: () => import("@/pages/Models"),
		},
	];

	afterEach(() => {
		cleanup();
	});

	beforeEach(() => {
		resetStableMocks();
		// Never-resolving IPC: the initial load stays pending so the
		// first-load skeleton state persists deterministically (no
		// waitFor / timing dependence).
		stableMocks.mockCall.mockImplementation(() => new Promise<never>(() => {}));
	});

	it.each(PAGE_CASES)(
		"$name renders a first-load Skeleton region (aria-busy, a11y.loading name)",
		async ({ load }) => {
			const mod = await load();
			const Page = mod.default;
			render(<Page />);

			// The skeleton's accessible name is the generic loading
			// message and it carries aria-busy — the single loading
			// contract shared by every page composition.
			const status = screen.getByRole("status", {
				name: "[t]a11y.loading",
			});
			expect(status).toHaveAttribute("aria-busy", "true");
			// A real skeleton shape is on screen (pulsing placeholder
			// blocks), not a spinner glyph.
			expect(document.querySelector('[data-slot="skeleton"]')).not.toBeNull();
		},
	);
});

describe("loading i18n keys exist in ALL 8 locale files", () => {
	it.each(LOADING_KEYS)("locale catalogue contains `%s`", (key) => {
		const missing: string[] = [];
		for (const [locale, catalogue] of Object.entries(LOCALES)) {
			if (!hasKey(catalogue, key)) {
				missing.push(locale);
			}
		}
		expect(missing).toEqual([]);
	});
});
