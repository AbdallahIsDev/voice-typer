/**
 * Page-shell consistency — every page-level state screen uses the SAME shell.
 *
 * The app has one page shell, and 20+ places declare it verbatim:
 *
 *   mx-auto flex min-h-full w-full max-w-4xl flex-col gap-6 px-16 pt-20 pb-6
 *
 * `mx-auto` + `max-w-4xl` give the 896px measure (DESIGN-SYSTEM.md §12),
 * `px-16` the 64px side padding, `pt-20 pb-6` the 80/24 vertical rhythm.
 * A branch that renders its own container and forgets that shell is
 * therefore *visually* wrong while remaining perfectly valid JSX — jsdom
 * does no layout, so nothing else in the suite can see it.
 *
 * That is exactly what happened on the Models load-failure screen: it
 * rendered `flex h-full items-center justify-center`, so the error card
 * stretched edge-to-edge across the content pane and its 8% border touched
 * the window chrome — the only full-bleed surface in the app. Settings had
 * the same defect in a subtler form: it reused the full-window
 * `ConnectionStatusScreen` shell (`max-w-lg px-6 py-12`), so its error card
 * was a different width AND sat 68px higher than the page it replaced.
 *
 * Verified geometry (Chromium, pane 762x662, the real one measured off the
 * user's screenshot): off-shell card 762px wide, 0px inset; page-shell card
 * 634px wide, 64px inset each side.
 *
 * This is a source-shape guard on purpose: the contract is a class string,
 * and the failure mode is invisible to every layout-free renderer.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const PAGES = resolve(__dirname, "..");

/** The shell every page-level state screen must use. */
const STATE_SHELL =
	"mx-auto flex min-h-full w-full max-w-4xl flex-col items-center justify-center gap-6 px-16 pt-20 pb-6";

/**
 * The off-shell container this guard exists to prevent: full height,
 * centred, and no measure or horizontal padding at all.
 */
const OFF_SHELL = "flex h-full items-center justify-center";

/**
 * Sanctioned exception. Onboarding's loading branch wraps a bare
 * `<Spinner />` in this container — there is no card, so there is nothing
 * to inset or constrain, and the flow is deliberately its own narrow
 * centred layout (ONB-3). Every other page-level use of the off-shell
 * pattern is the bug.
 */
const ALLOWED = new Set(["Onboarding.tsx"]);

/** The three state screens that replaced a page and were off-shell. */
const FIXED: [file: string, branch: string][] = [
	["Models.tsx", "load-failure EmptyState"],
	["Settings.tsx", "load-failure EmptyState"],
	["Dashboard.tsx", "fetch-failure EmptyState"],
];

function pageFiles(dir: string, out: string[] = []): string[] {
	for (const entry of readdirSync(dir)) {
		if (entry === "__tests__" || entry === "node_modules") continue;
		const full = resolve(dir, entry);
		if (statSync(full).isDirectory()) pageFiles(full, out);
		else if (entry.endsWith(".tsx")) out.push(full);
	}
	return out;
}

/** Strip comments — these files explain the shells in prose. */
function stripComments(source: string): string {
	return source
		.replace(/\/\*[\s\S]*?\*\//g, "")
		.replace(/(^|[^:])\/\/[^\n]*/g, "$1");
}

const FILES = pageFiles(PAGES).map((f) => ({
	name: f.slice(PAGES.length + 1).replace(/\\/g, "/"),
	text: stripComments(readFileSync(f, "utf8")),
}));

describe("page shell — one shell for every page-level state screen", () => {
	it("has pages to check (the walk is not vacuous)", () => {
		expect(FILES.length).toBeGreaterThan(10);
	});

	it("no page renders the off-shell full-bleed container", () => {
		const offenders = FILES.filter((f) => f.text.includes(OFF_SHELL))
			.map((f) => f.name)
			.filter((name) => !ALLOWED.has(name));
		expect(
			offenders,
			`use the page shell (${STATE_SHELL}) in: ${offenders.join(", ")}`,
		).toEqual([]);
	});

	for (const [file, branch] of FIXED) {
		it(`${file} — ${branch} sits in the page shell`, () => {
			const page = FILES.find((f) => f.name === file);
			expect(page, `${file} not found under pages/`).toBeTruthy();
			expect(page?.text).toContain(STATE_SHELL);
		});
	}

	it("the shell the guard demands is the shell the pages use", () => {
		// Guard the guard: if STATE_SHELL ever stops matching real markup,
		// the assertions above would pass vacuously against a typo. The
		// canonical loaded-page shell (no centring) must also be present.
		const loaded = FILES.filter((f) =>
			f.text.includes(
				"mx-auto flex min-h-full w-full max-w-4xl flex-col gap-6 px-16 pt-20 pb-6",
			),
		);
		expect(loaded.length).toBeGreaterThan(5);
	});
});
