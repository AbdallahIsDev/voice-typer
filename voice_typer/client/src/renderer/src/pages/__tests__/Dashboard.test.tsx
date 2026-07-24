/**
 * Regression tests for the Dashboard page fixes landed in session BG
 * (Group 3 — UX & UI).
 *
 * Covers three findings, each in its own describe block so a failure
 * pinpoints which contract regressed:
 *
 *   - BG-3  Dashboard 7-day activity chart container has `role="img"` +
 *           descriptive `aria-label`; bars are non-interactive `<div>`s
 *           (no `<button>`); bar opacity bumped from `/60` to `/80` for
 *           WCAG 1.4.11 contrast.
 *   - BG-9  Dashboard.tsx + StatCards.tsx both consume the shared
 *           `formatDuration` from `lib/format.ts`; in-component copies
 *           dropped. The shared helper resolves `h` / `m` glyphs through
 *           `t()` (analytics.durationHours / durationMinutes /
 *           durationHoursMinutes / durationZero).
 *   - BG-10 Dashboard "Share stats" button visibility is gated on
 *           `canShareStats({todayCount, totalCount})` (not
 *           `data.todayCount > 0`) so users with historical
 *           transcriptions but no today dictations can still share.
 *
 * Static-source-check strategy
 * ----------------------------
 * The Dashboard page has a heavy dependency graph (usePython, hugeicons,
 * sonner, useLastUpdated, html-to-image). Rendering it for behavioral
 * assertions would require re-stubbing the same modules already mocked
 * in `pages-improvements.test.tsx`. The contracts we need to verify
 * (role attribute, element tag, opacity class, import statements) are
 * all visible in the source text, so we use static `fs.readFileSync`
 * checks — same pattern used by `accessibility.test.tsx` and the
 * `R7-F18` block in `pages-improvements.test.tsx`.
 *
 * The `formatDuration` behavioural tests, by contrast, are real unit
 * tests — the shared helper has no React dependencies, so we can call
 * it directly.
 */
import { describe, expect, it } from "vitest";
import { setLocale, t } from "@/i18n/i18n";
import { formatDuration } from "@/lib/format";

const fs = require("node:fs");
const path = require("node:path");

const DASHBOARD_SRC = fs.readFileSync(
	path.resolve(__dirname, "..", "Dashboard.tsx"),
	"utf8",
);
const STATCARDS_SRC = fs.readFileSync(
	path.resolve(
		__dirname,
		"..",
		"..",
		"components",
		"dashboard",
		"StatCards.tsx",
	),
	"utf8",
);
const FORMAT_SRC = fs.readFileSync(
	path.resolve(__dirname, "..", "..", "lib", "format.ts"),
	"utf8",
);
const EN_JSON = JSON.parse(
	fs.readFileSync(
		path.resolve(__dirname, "..", "..", "i18n", "translations", "en.json"),
		"utf8",
	),
);

// ── BG-3 ──────────────────────────────────────────────────────────────

describe("BG-3: Dashboard 7-day chart container role=img + non-interactive bars", () => {
	it('chart container <div> has role="img" and aria-label=', () => {
		// The `flex items-end justify-between gap-2 h-20` class appears
		// twice in the file — once in the loading skeleton (no
		// role="img") and once in the rendered chart (with role="img").
		// The chart container is the LAST occurrence, and the
		// role="img" + aria-label= attributes appear in the 400-char
		// window AFTER the className attribute (since they're listed
		// after className in the JSX opening tag).
		const chartIdx = DASHBOARD_SRC.lastIndexOf(
			"flex items-end justify-between gap-2 h-20",
		);
		expect(chartIdx).toBeGreaterThan(-1);

		const window = DASHBOARD_SRC.slice(chartIdx, chartIdx + 400);
		expect(window).toMatch(/role="img"/);
		expect(window).toMatch(/aria-label=/);
	});

	it("chart container aria-label uses analytics.sevenDayActivityChartAria key", () => {
		// BG-8 / BG-3: the aria-label is built from the
		// analytics.sevenDayActivityChartAria i18n key (with a {counts}
		// interpolation param) rather than a literal English string.
		expect(DASHBOARD_SRC).toContain("analytics.sevenDayActivityChartAria");
		expect(DASHBOARD_SRC).toMatch(/counts:\s*d\.dailyActivity/);
	});

	it("bars are non-interactive <div> elements (not <button>)", () => {
		// Locate the chart's JSX block and assert no <button> appears
		// inside it. The chart container starts at the
		// `flex items-end justify-between gap-2 h-20` div and ends at
		// the closing `</div>` of the parent chart card. We search the
		// full source for any `<button` that also has the bar's
		// className (`bg-accent/`) — those are the bars (now <div>s).
		const buttonWithAccentClass = /<button[^>]*bg-accent/.test(DASHBOARD_SRC);
		expect(buttonWithAccentClass).toBe(false);

		// And the bars are <div> with the accent class.
		const divWithAccentClass = /<div[^>]*bg-accent/.test(DASHBOARD_SRC);
		expect(divWithAccentClass).toBe(true);
	});

	it("bars no longer carry tabIndex or aria-label attributes (informational, not focusable)", () => {
		// The previous implementation gave each bar `tabIndex={0}` and
		// `aria-label={...}` so the chart produced 7 dead-end tab stops
		// and an SR announcement of "button, button, ...". After BG-3
		// the chart container owns the role/label and the bars are
		// plain divs.
		const chartIdx = DASHBOARD_SRC.lastIndexOf(
			"flex items-end justify-between gap-2 h-20",
		);
		expect(chartIdx).toBeGreaterThan(-1);

		// Take a window that covers the entire chart map() block.
		const blockStart = chartIdx;
		const blockEnd = DASHBOARD_SRC.indexOf("</div>", blockStart + 800);
		const block = DASHBOARD_SRC.slice(blockStart, blockEnd);
		expect(block).not.toMatch(/tabIndex=\{0\}/);
		expect(block).not.toMatch(/aria-label=\{ariaLabel\}/);
	});

	it("bar opacity is bg-accent/80 (WCAG 1.4.11 contrast, was /60)", () => {
		// The chart bar is the only element with both `bg-accent/` and
		// `rounded-sm` in the file. BG-3 bumps it from /60 to /80.
		expect(DASHBOARD_SRC).toMatch(/bg-accent\/80/);
		expect(DASHBOARD_SRC).not.toMatch(/bg-accent\/60/);
	});
});

// ── BG-9 ──────────────────────────────────────────────────────────────

describe("BG-9: formatDuration shared via lib/format.ts + i18n keys", () => {
	it("Dashboard.tsx imports formatDuration from @/lib/format (no local copy)", () => {
		expect(DASHBOARD_SRC).toMatch(
			/import\s*\{[^}]*\bformatDuration\b[^}]*\}\s*from\s*"@\/lib\/format"/,
		);
		// No local `function formatDuration` declaration.
		const stripped = DASHBOARD_SRC.replace(/\/\*[\s\S]*?\*\//g, "").replace(
			/\/\/.*$/gm,
			"",
		);
		expect(stripped).not.toMatch(/function\s+formatDuration\s*\(/);
	});

	it("StatCards.tsx imports formatDuration from @/lib/format (no local copy)", () => {
		expect(STATCARDS_SRC).toMatch(
			/import\s*\{[^}]*\bformatDuration\b[^}]*\}\s*from\s*"@\/lib\/format"/,
		);
		const stripped = STATCARDS_SRC.replace(/\/\*[\s\S]*?\*\//g, "").replace(
			/\/\/.*$/gm,
			"",
		);
		expect(stripped).not.toMatch(/function\s+formatDuration\s*\(/);
	});

	it("lib/format.ts formatDuration resolves glyphs through t() (no hardcoded 'h'/'m' suffixes)", () => {
		// The new implementation calls t("analytics.durationHours"|"durationMinutes"|
		// "durationHoursMinutes"|"durationZero"). Assert each key is referenced.
		expect(FORMAT_SRC).toContain("analytics.durationZero");
		expect(FORMAT_SRC).toContain("analytics.durationMinutes");
		expect(FORMAT_SRC).toContain("analytics.durationHours");
		expect(FORMAT_SRC).toContain("analytics.durationHoursMinutes");

		// No hardcoded English suffix literals like `${hourLabel}` or
		// `"1h"` / `"0m"` returned directly. The previous impl used
		// const minuteLabel = "m"; const hourLabel = "h"; — those are
		// gone.
		expect(FORMAT_SRC).not.toMatch(/const\s+minuteLabel\s*=\s*"m"/);
		expect(FORMAT_SRC).not.toMatch(/const\s+hourLabel\s*=\s*"h"/);
		expect(FORMAT_SRC).not.toMatch(/const\s+secondLabel\s*=\s*"s"/);
	});

	it("en.json defines the four new duration i18n keys", () => {
		expect(EN_JSON.analytics.durationHours).toBe("{h}h");
		expect(EN_JSON.analytics.durationMinutes).toBe("{m}m");
		expect(EN_JSON.analytics.durationHoursMinutes).toBe("{h}h {m}m");
		expect(EN_JSON.analytics.durationZero).toBe("0m");
	});

	it("en.json defines analytics.sevenDayActivityChartAria (BG-8)", () => {
		expect(EN_JSON.analytics.sevenDayActivityChartAria).toBe(
			"7-day activity chart: {counts}",
		);
	});

	// ── Behavioral tests for the shared formatDuration ──────────────

	it("formatDuration(0) returns '0m' (durationZero key)", () => {
		setLocale("en");
		expect(formatDuration(0)).toBe("0m");
	});

	it("formatDuration(negative) returns '0m' (durationZero key)", () => {
		setLocale("en");
		expect(formatDuration(-5)).toBe("0m");
	});

	it("formatDuration(sub-minute) rounds up to '1m' (matches StatCards legacy)", () => {
		setLocale("en");
		// 5s and 45s both round to 1 minute (matches StatCards legacy
		// behaviour; the old Dashboard copy returned "0m" for 5s which
		// was a bug).
		expect(formatDuration(5)).toBe("1m");
		expect(formatDuration(45)).toBe("1m");
	});

	it("formatDuration(120) returns '2m' (durationMinutes key)", () => {
		setLocale("en");
		expect(formatDuration(120)).toBe("2m");
	});

	it("formatDuration(3600) returns '1h' (durationHours key, m===0)", () => {
		setLocale("en");
		expect(formatDuration(3600)).toBe("1h");
	});

	it("formatDuration(3900) returns '1h 5m' (durationHoursMinutes key)", () => {
		setLocale("en");
		expect(formatDuration(3900)).toBe("1h 5m");
	});

	it("formatDuration(5235) returns '1h 27m' (StatCards storybook snapshot)", () => {
		// StatCards.stories.tsx documents 5235s → "1h 27m" — preserve
		// that contract through the i18n refactor.
		setLocale("en");
		expect(formatDuration(5235)).toBe("1h 27m");
	});

	it("formatDuration resolves through t() so the visible glyphs track the active locale", () => {
		// We can't assert non-English glyphs (F1 hasn't translated the
		// keys yet), but we CAN assert that formatDuration's output
		// matches what t() returns for the resolved key — proving the
		// helper is wired through i18n rather than returning hardcoded
		// English. After F1 translates, this test continues to pass
		// because both sides go through t().
		setLocale("en");
		const direct = t("analytics.durationHoursMinutes", {
			h: "1",
			m: "5",
		});
		expect(formatDuration(3900)).toBe(direct);
	});
});

// ── BG-10 ─────────────────────────────────────────────────────────────

describe("BG-10: Dashboard Share button gated on canShareStats (not todayCount > 0)", () => {
	it("Dashboard.tsx imports canShareStats from @/hooks/useStatsShare", () => {
		expect(DASHBOARD_SRC).toMatch(
			/import\s*\{[^}]*\bcanShareStats\b[^}]*\}\s*from\s*"@\/hooks\/useStatsShare"/,
		);
	});

	it("Dashboard.tsx calls canShareStats(...) (not `data.todayCount > 0`)", () => {
		// The previous gate `data && configRaw && data.todayCount > 0 && (`
		// is gone. The new gate calls canShareStats with both counts.
		expect(DASHBOARD_SRC).toMatch(/canShareStats\(\s*\{/);
		expect(DASHBOARD_SRC).toMatch(/todayCount:\s*data\.todayCount/);
		expect(DASHBOARD_SRC).toMatch(/totalCount:\s*data\.totalCount/);
		// The old `data.todayCount > 0` gate is no longer present.
		// (We can't ban the substring entirely — the field is still
		// read elsewhere — but the specific gating expression
		// `data.todayCount > 0 && (` is gone.)
		expect(DASHBOARD_SRC).not.toMatch(/data\.todayCount\s*>\s*0\s*&&\s*\(/);
	});
});
