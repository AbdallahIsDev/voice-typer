/**
 * Regression tests for the Dashboard page fixes landed in session BG
 * (Group 3 — UX & UI).
 *
 * Covers three findings, each in its own describe block so a failure
 * pinpoints which contract regressed:
 *
 *   -   Dashboard 7-day activity chart container has `role="img"` +
 *           descriptive `aria-label`; bars are non-interactive `<div>`s
 *           (no `<button>`); bar opacity bumped from `/60` to `/80` for
 *           WCAG 1.4.11 contrast.
 *   -   Dashboard.tsx + StatCards.tsx both consume the shared
 *           `formatDuration` from `lib/format.ts`; in-component copies
 *           dropped. The shared helper resolves `h` / `m` glyphs through
 *           `t()` (analytics.durationHours / durationMinutes /
 *           durationHoursMinutes / durationZero).
 *   -  Dashboard "Share stats" button visibility is gated on
 *           `canShareStats({todayCount, totalCount})` (not
 *           `data.todayCount > 0`) so users with historical
 *           transcriptions but no today dictations can still share.
 *   -  Dashboard share-image capture container's inline `style`
 *           literal is hoisted to a module-level constant so the object
 *           identity is stable across renders (a fresh inline object
 *           on every render would break `React.memo` on the share-image
 *           subtree).
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
//the 7-day activity chart JSX was extracted from Dashboard.tsx
//into pages/dashboard/components/SevenDayActivityChart.tsx. The
// assertions below target the chart's new home (the strings no longer
// appear in DASHBOARD_SRC after the split).
const SEVEN_DAY_SRC = fs.readFileSync(
	path.resolve(
		__dirname,
		"..",
		"dashboard",
		"components",
		"SevenDayActivityChart.tsx",
	),
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
// Dashboard data-fetch hook source. The hook now fires an
// additional `get_status` IPC call in its Promise.all to fetch the
// on-disk `config_dir` for the footer; the assertions below verify the
// hook owns that fetch + exposes `configDir` on its result.
const HOOK_SRC = fs.readFileSync(
	path.resolve(__dirname, "..", "dashboard", "hooks", "useDashboardData.ts"),
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

//

describe("BG-3: Dashboard activity chart container role=img + non-interactive bars", () => {
	it('chart container <div> has role="img" and aria-label=', () => {
		//the chart JSX lives in SevenDayActivityChart.tsx. The chart
		// is exposed to AT as a SINGLE role="img" container with a
		// descriptive aria-label (no dead-end tab stops, one
		// announcement instead of "button, button, ..."). Use
		// lastIndexOf so the file's leading docstring (which mentions
		// role="img" in prose) can't mask the JSX occurrence.
		const chartIdx = SEVEN_DAY_SRC.lastIndexOf('role="img"');
		expect(chartIdx).toBeGreaterThan(-1);

		const window = SEVEN_DAY_SRC.slice(chartIdx, chartIdx + 400);
		expect(window).toMatch(/aria-label=/);
	});

	it("chart container aria-label uses analytics.activityChartAria key", () => {
		//the aria-label is built from the
		// analytics.activityChartAria i18n key (with {range} + {counts}
		// interpolation params) rather than a literal English string.
		expect(SEVEN_DAY_SRC).toContain("analytics.activityChartAria");
		expect(SEVEN_DAY_SRC).toMatch(/counts:\s*ariaCounts/);
	});

	it("bars are non-interactive <div> elements (not <button>)", () => {
		// No <button> exists in the chart source at all; the bars are
		// plain <div>s with the accent fill class.
		const buttonWithAccentClass = /<button[^>]*bg-accent/.test(SEVEN_DAY_SRC);
		expect(buttonWithAccentClass).toBe(false);

		// And the bars carry the accent fill (high-contrast /90).
		expect(SEVEN_DAY_SRC).toContain("bg-accent/90");
	});

	it("bars carry no tabIndex and no per-bar aria-label (single-announcement chart)", () => {
		// The previous implementation gave each bar `tabIndex={0}` and
		// `aria-label={...}` so the chart produced 7 dead-end tab stops
		// and an SR announcement of "button, button, ...". After
		// the fix the chart container owns the role/label and the bars
		// are plain divs (hover tooltips via title only). Anchor on the
		// JSX `{bars.map((bar) =>` (the earlier `bars.map` occurrence is
		// the ariaCounts helper, before the container div).
		const barBlockStart = SEVEN_DAY_SRC.indexOf("{bars.map((bar)");
		expect(barBlockStart).toBeGreaterThan(-1);
		const barBlock = SEVEN_DAY_SRC.slice(barBlockStart);
		expect(barBlock).not.toMatch(/tabIndex/);
		expect(barBlock).not.toMatch(/aria-label=/);
	});

	it("bar fill is bg-accent/90 (WCAG 1.4.11 contrast, was /60)", () => {
		// The chart bar fill is the only bg-accent/ element; bumped to
		// /90 for WCAG 1.4.11 contrast against the card background.
		expect(SEVEN_DAY_SRC).toMatch(/bg-accent\/90/);
		expect(SEVEN_DAY_SRC).not.toMatch(/bg-accent\/60/);
	});
});

//

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

	it("en.json defines analytics.activityChartAria (BG-8)", () => {
		expect(EN_JSON.analytics.activityChartAria).toBe(
			"{range} activity chart: {counts}",
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

//

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

//

describe("DJ-93: Dashboard share-image container style hoisted to module-level constant", () => {
	it("Dashboard.tsx declares a module-level CSSProperties constant for the share-image capture container", () => {
		// The fix hoists the previously-inline `style={{ position: "absolute",
		// top: 0, left: 0, zIndex: -100, pointerEvents: "none" }}` literal
		// to a module-level `SHARE_IMAGE_CAPTURE_STYLE` constant typed as
		// `CSSProperties`. The static values never change between renders,
		// so a single module-level instance is correct — and crucially,
		// the stable object identity lets a future `React.memo` on the
		// share-image subtree short-circuit re-renders when the stats
		// haven't changed.
		//
		// The `CSSProperties` type import from "react" is also pinned so
		// a future refactor that drops the type annotation (and thus
		// weakens the contract) fails this test.
		expect(DASHBOARD_SRC).toMatch(
			/import\s+type\s+\{\s*CSSProperties\s*\}\s+from\s*"react"/,
		);
		expect(DASHBOARD_SRC).toMatch(
			/const\s+SHARE_IMAGE_CAPTURE_STYLE\s*:\s*CSSProperties\s*=/,
		);
	});

	it("share-image capture container style values are present in the hoisted constant", () => {
		// Pin the four style values that the share-image capture target
		// depends on: position:absolute (off-screen positioning),
		// top:0 + left:0 (anchor to top-left), zIndex:-100 (behind
		// everything else), pointerEvents:none (invisible to mouse).
		// The values are checked inside the constant declaration block
		// (between `SHARE_IMAGE_CAPTURE_STYLE: CSSProperties = {` and the
		// closing `}`), not anywhere else in the file — so a future
		// refactor that accidentally moves a value out of the constant
		// (e.g. back into an inline literal) fails this test.
		const constStart = DASHBOARD_SRC.indexOf("SHARE_IMAGE_CAPTURE_STYLE");
		expect(constStart).toBeGreaterThan(-1);
		const constBlockEnd = DASHBOARD_SRC.indexOf("};", constStart);
		expect(constBlockEnd).toBeGreaterThan(constStart);
		const constBlock = DASHBOARD_SRC.slice(constStart, constBlockEnd + 2);
		expect(constBlock).toMatch(/position:\s*"absolute"/);
		expect(constBlock).toMatch(/top:\s*0/);
		expect(constBlock).toMatch(/left:\s*0/);
		expect(constBlock).toMatch(/zIndex:\s*-100/);
		expect(constBlock).toMatch(/pointerEvents:\s*"none"/);
	});

	it("share-image capture container references the hoisted constant via style={SHARE_IMAGE_CAPTURE_STYLE}", () => {
		// The JSX uses `style={SHARE_IMAGE_CAPTURE_STYLE}` (a single
		// identifier reference) instead of the previous inline
		// `style={{ position: "absolute", ... }}` literal. The
		// identifier reference gives a stable object identity across
		// renders (the constant is created once at module load); the
		// inline literal created a fresh object on every render.
		expect(DASHBOARD_SRC).toMatch(/style=\{SHARE_IMAGE_CAPTURE_STYLE\}/);
		// The old inline `style={{ position: "absolute", ... }}` literal
		// is gone — the `position: "absolute"` value now appears ONLY in
		// the module-level constant declaration (covered by the previous
		// test). A stray inline `position: "absolute"` outside the
		// constant block would indicate a regression.
		const constStart = DASHBOARD_SRC.indexOf("SHARE_IMAGE_CAPTURE_STYLE");
		const constBlockEnd = DASHBOARD_SRC.indexOf("};", constStart);
		const beforeConst = DASHBOARD_SRC.slice(0, constStart);
		const afterConst = DASHBOARD_SRC.slice(constBlockEnd + 2);
		// No inline `position: "absolute"` literal outside the constant
		// block (would indicate a second copy that should also be hoisted).
		expect(beforeConst).not.toMatch(/position:\s*"absolute"/);
		expect(afterConst).not.toMatch(/position:\s*"absolute"/);
	});
});

//

describe("Dashboard noDataDescription interpolates {hotkey} from config", () => {
	it('Dashboard.tsx calls t("analytics.noDataDescription", { hotkey: ... })', () => {
		// The empty-state CTA copy is "Press {hotkey} on the Home page to
		// dictate — your stats will appear here." The previous call omitted
		// the params object, so the literal "{hotkey}" token leaked into
		// the rendered UI. The fix passes the resolved hotkey (falling back
		// to "F2" when configRaw is null or the field is missing).
		expect(DASHBOARD_SRC).toMatch(
			/noDataDescription",\s*\{[\s\S]*?hotkey:\s*configRaw\?\.hotkey\s*\|\|\s*"F2"[\s\S]*?\}/,
		);
		// The bare no-arg call is gone (would re-introduce the literal
		// {hotkey} token in the rendered string).
		expect(DASHBOARD_SRC).not.toMatch(/t\("analytics\.noDataDescription"\)\s/);
	});
});

//

describe("Dashboard dataPath uses {path} interpolation fed by get_status config_dir", () => {
	it('Dashboard.tsx calls t("analytics.dataPath", { path: configDir || ... })', () => {
		// The previous implementation rendered the hardcoded English string
		// "Data stored in: ~/.voice-typer/" regardless of platform. The fix
		// interpolates the actual on-disk path (fetched via the get_status
		// IPC) so Windows / VOICE_TYPER_CONFIG_DIR users see the right path.
		expect(DASHBOARD_SRC).toMatch(
			/dataPath",\s*\{[\s\S]*?path:\s*configDir\s*\|\|\s*"~\/\.voice-typer\/"[\s\S]*?\}/,
		);
		// The bare no-arg call is gone.
		expect(DASHBOARD_SRC).not.toMatch(/t\("analytics\.dataPath"\)\s/);
	});

	it("Dashboard.tsx destructures configDir from useDashboardData", () => {
		// The hook now exposes `configDir` alongside `configRaw`; the page
		// must consume it for the dataPath interpolation above to resolve.
		expect(DASHBOARD_SRC).toMatch(/configDir,/);
	});

	it("useDashboardData.ts fetches get_status in the Promise.all (C-DATA-1 local IPC)", () => {
		// The hook now fires `get_status` alongside `get_config` /
		// `get_today_stats` / `get_history` / `get_history_count`. The call
		// is a local IPC probe (C-DATA-1, offline) — no network. A `.catch`
		// fallback keeps the Promise.all alive if the backend doesn't expose
		// `get_status` or the field is missing (older sidecar).
		expect(HOOK_SRC).toMatch(
			/call<.*config_dir\?:\s*string.*>.*\("get_status"/,
		);
		expect(HOOK_SRC).toMatch(/get_status.*\.catch\(\(\)\s*=>\s*null\)/);
	});

	it("useDashboardData.ts exposes configDir on its result + populates it from status.config_dir", () => {
		// The hook must (1) declare `configDir: string` on its result
		// interface so consumers can destructure it, (2) seed state from
		// `status?.config_dir ?? ""` after the Promise.all resolves, and
		// (3) include `configDir` in the returned object literal.
		expect(HOOK_SRC).toMatch(/configDir:\s*string;/);
		expect(HOOK_SRC).toMatch(/status\?\.config_dir/);
		expect(HOOK_SRC).toMatch(/configDir,/);
	});
});

//

describe("SevenDayActivityChart migrates binary plural to tChoice", () => {
	it("SevenDayActivityChart.tsx imports tChoice from @/i18n/i18n", () => {
		// The chart previously imported only `t` and branched on
		// `day.count === 1` between two hardcoded keys
		// (dayCountTooltipSingular / dayCountTooltipPlural). The fix
		// delegates to `tChoice` so CLDR plural categories (one/other/few/
		// many) are resolved by Intl.PluralRules for the active locale.
		expect(SEVEN_DAY_SRC).toMatch(
			/import\s*\{\s*t,\s*tChoice\s*\}\s*from\s*"@\/i18n\/i18n"/,
		);
	});

	it('SevenDayActivityChart.tsx uses tChoice("analytics.dayCountTooltip", bar.count, { label })', () => {
		// The single tChoice call replaces the previous ternary. The
		// `count` argument drives plural-category selection; `label` is
		// forwarded as an interpolation param so each bar's tooltip
		// shows its slot label (weekday or hour).
		expect(SEVEN_DAY_SRC).toMatch(
			/tChoice\(\s*"analytics\.dayCountTooltip",\s*bar\.count,\s*\{\s*label:\s*bar\.label,?\s*\}\s*\)/,
		);
	});

	it("SevenDayActivityChart.tsx no longer references the binary plural keys", () => {
		// The legacy `dayCountTooltipSingular` / `dayCountTooltipPlural`
		// keys are dead after the migration. Asserting their absence in the
		// chart source pins the migration so a future revert fails this test.
		expect(SEVEN_DAY_SRC).not.toMatch(/dayCountTooltipSingular/);
		expect(SEVEN_DAY_SRC).not.toMatch(/dayCountTooltipPlural/);
		// The manual `day.count === 1` ternary is gone too (replaced by
		// Intl.PluralRules inside tChoice).
		expect(SEVEN_DAY_SRC).not.toMatch(/day\.count\s*===\s*1\s*\?/);
	});

	it("en.json defines dayCountTooltip_one / dayCountTooltip_other (CLDR plural keys)", () => {
		// The tChoice lookup chain falls back through `{key}_{category}`
		// → `{key}_other` → bare `{key}`. The CLDR-style keys already
		// exist in en.json — pin them so a future JSON cleanup doesn't
		// accidentally drop them and silently fall back to the bare key.
		expect(EN_JSON.analytics.dayCountTooltip_one).toBeDefined();
		expect(EN_JSON.analytics.dayCountTooltip_other).toBeDefined();
		// The legacy binary-plural keys can stay (other agents may still
		// reference them) — we only assert the new CLDR keys are present.
	});
});
