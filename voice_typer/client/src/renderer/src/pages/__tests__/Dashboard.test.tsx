import { describe, expect, it } from "vitest";
import { setLocale, t } from "@/i18n/i18n";
import { formatDuration } from "@/lib/format";

const fs = require("node:fs");
const path = require("node:path");

const DASHBOARD_SRC = fs.readFileSync(
	path.resolve(__dirname, "..", "Dashboard.tsx"),
	"utf8",
);
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
// Dashboard data-fetch hook source. The assertions below read it to
// pin the hook's data plumbing (correction-usage fetch, model-status
// resolution) without rendering the component.
const HOOK_SRC = fs.readFileSync(
	path.resolve(__dirname, "..", "dashboard", "hooks", "useDashboardData.ts"),
	"utf8",
);
// the SHARED model-install truth (lib/utils/models.ts), its source is
// read here so the point-10 assertions can verify the `downloaded`
// check lives in ONE place (the shared helper), not in the hook.
const MODELS_SRC = fs.readFileSync(
	path.resolve(__dirname, "..", "..", "lib", "utils", "models.ts"),
	"utf8",
);

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

		// And the bars carry the accent fill at full strength.
		expect(SEVEN_DAY_SRC).toContain('"bg-accent"');
	});

	it("bars carry no tabIndex and no per-bar aria-label (single-announcement chart)", () => {
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

	it("bar fill is a flat, fully opaque bg-accent (no alpha step, no hover)", () => {
		// The bar is a read-out, not a control: it renders at full
		// strength and looks identical whether or not the pointer is
		// over it. (It was bg-accent/90 + hover:bg-accent — every bar
		// sat dimmed until hovered.)
		expect(SEVEN_DAY_SRC).toContain('"bg-accent"');
		expect(SEVEN_DAY_SRC).not.toMatch(/bg-accent\//);
		expect(SEVEN_DAY_SRC).not.toMatch(/hover:bg-accent/);
	});
});

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

	// ── Behavioral tests for the shared formatDuration ──────────────

	it("formatDuration(0) returns '0' (no unit suffix)", () => {
		setLocale("en");
		expect(formatDuration(0)).toBe("0");
	});

	it("formatDuration(negative) returns '0'", () => {
		setLocale("en");
		expect(formatDuration(-5)).toBe("0");
	});

	it("formatDuration(sub-minute) rounds up to '1m' (matches StatCards legacy)", () => {
		setLocale("en");
		// 5s and 45s both round to 1 minute (matches StatCards legacy
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
		// StatCards.stories.tsx documents 5235s → "1h 27m", preserve
		// that contract through the i18n refactor.
		setLocale("en");
		expect(formatDuration(5235)).toBe("1h 27m");
	});

	it("formatDuration resolves through t() so the visible glyphs track the active locale", () => {
		// keys yet), but we CAN assert that formatDuration's output
		// matches what t() returns for the resolved key, proving the
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

describe("BG-10: Dashboard Share button always mounted, disabled without data (C-CACHE-6)", () => {
	it("Dashboard.tsx imports canShareStats from @/hooks/useStatsShare", () => {
		expect(DASHBOARD_SRC).toMatch(
			/import\s*\{[^}]*\bcanShareStats\b[^}]*\}\s*from\s*"@\/hooks\/useStatsShare"/,
		);
	});

	it("ShareStatsDialog is always rendered with a disabled prop (never &&-gated)", () => {
		// The trigger must stay mounted while loading and flip to
		// enabled when data lands: no conditional wrapper around it.
		expect(DASHBOARD_SRC).toMatch(/<ShareStatsDialog[\s\S]*?disabled=\{/);
		expect(DASHBOARD_SRC).not.toMatch(/&& \(\s*<ShareStatsDialog/);
	});

	it("disabled derives from missing data/config or !canShareStats(...)", () => {
		// The previous gate `data && configRaw && data.todayCount > 0 && (`
		// is gone. Availability is a boolean feeding `disabled`.
		expect(DASHBOARD_SRC).toMatch(/shareDisabled/);
		expect(DASHBOARD_SRC).toMatch(/!canShareStats\(\s*\{/);
		expect(DASHBOARD_SRC).toMatch(/todayCount:\s*data\?\.todayCount/);
		expect(DASHBOARD_SRC).toMatch(/totalCount:\s*data\?\.totalCount/);
		// (We can't ban the substring entirely, the field is still
		// read elsewhere, but the specific gating expression
		// `data.todayCount > 0 && (` is gone.)
		expect(DASHBOARD_SRC).not.toMatch(/data\.todayCount\s*>\s*0\s*&&\s*\(/);
	});

	it("first-load path renders the real heading + disabled Share above the skeleton body", () => {
		// Actions never vanish mid-load; the body (not the full
		// skeleton with its heading placeholder) renders below.
		expect(DASHBOARD_SRC).toMatch(/DashboardSkeletonBody/);
		const earlyReturn = DASHBOARD_SRC.slice(
			DASHBOARD_SRC.indexOf("if (!data) {"),
		);
		expect(earlyReturn).toMatch(/<ShareStatsDialog[\s\S]*?disabled[\s\S]*?\/>/);
	});
});

describe("DJ-93: Dashboard share-image container style hoisted to module-level constant", () => {
	it("Dashboard.tsx declares a module-level CSSProperties constant for the share-image capture container", () => {
		// top: 0, left: 0, zIndex: -100, pointerEvents: "none" }}` literal
		// to a module-level `SHARE_IMAGE_CAPTURE_STYLE` constant typed as
		// `CSSProperties`. The static values never change between renders,
		// so a single module-level instance is correct, and crucially,
		// the stable object identity lets a future `React.memo` on the
		// share-image subtree short-circuit re-renders when the stats
		// haven't changed.
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
		// closing `}`), not anywhere else in the file, so a future
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
		// is gone, the `position: "absolute"` value now appears ONLY in
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

describe("Dashboard noDataDescription interpolates {hotkey} from config", () => {
	it('Dashboard.tsx calls t("analytics.noDataDescription", { hotkey: ... })', () => {
		// The empty-state CTA copy is "Press {hotkey} on the Home page to
		// dictate, your stats will appear here." The previous call omitted
		// the params object, so the literal "{hotkey}" token leaked into
		// the rendered UI. The call now passes a split marker plus the
		// resolved hotkey (falling back to "F2" when configRaw is null or
		// the field is missing), and the marker is swapped for HotkeyChips
		// so the shortcut renders as keycaps (C-UI-1) rather than raw
		// config syntax (`<caps_lock>`).
		expect(DASHBOARD_SRC).toMatch(
			/noDataDescription",\s*\{\s*hotkey:\s*NO_DATA_HOTKEY_MARKER\s*\}\)[\s\S]*?formatHotkey\(configRaw\?\.hotkey\s*\|\|\s*"F2"\)/,
		);
		// The marker is rendered through HotkeyChips, not as plain text.
		expect(DASHBOARD_SRC).toMatch(/renderNoDataDescription\(/);
		expect(DASHBOARD_SRC).toMatch(/<HotkeyChips keys=\{hotkey\} \/>/);
		// The bare no-arg call is gone (would re-introduce the literal
		// {hotkey} token in the rendered string).
		expect(DASHBOARD_SRC).not.toMatch(/t\("analytics\.noDataDescription"\)\s/);
	});
});

describe("SevenDayActivityChart migrates binary plural to tChoice", () => {
	it("SevenDayActivityChart.tsx imports tChoice from @/i18n/i18n", () => {
		// `day.count === 1` between two hardcoded keys
		// (dayCountTooltipSingular / dayCountTooltipPlural). The fix
		// delegates to `tChoice` so CLDR plural categories (one/other/few/
		// many) are resolved by Intl.PluralRules for the active locale.
		expect(SEVEN_DAY_SRC).toMatch(
			/import\s*\{\s*t,\s*tChoice\s*\}\s*from\s*"@\/i18n\/i18n"/,
		);
	});

	it('SevenDayActivityChart.tsx uses tChoice("analytics.heatmap.tooltip", bar.count)', () => {
		// The single tChoice call replaces the previous ternary. The
		// `count` argument drives plural-category selection. It carries
		// NO `label` param: the slot's weekday/hour is already printed
		// on the x axis under the bar, so the tooltip shows the count
		// alone ("3 dictations", not "Mon: 3 dictations").
		expect(SEVEN_DAY_SRC).toMatch(
			/tChoice\(\s*"analytics\.heatmap\.tooltip",\s*bar\.count,?\s*\)/,
		);
		expect(SEVEN_DAY_SRC).not.toMatch(/label:\s*bar\.label/);
		// One shape for every slot: an empty one reports "0 dictations"
		// rather than switching to a separate "no data" sentence, so the
		// key is off this chart entirely.
		expect(SEVEN_DAY_SRC).not.toMatch(/analytics\.noDataBar/);
	});

	it("SevenDayActivityChart.tsx no longer references the binary plural keys", () => {
		// The legacy `dayCountTooltipSingular` / `dayCountTooltipPlural`
		// keys are dead after the migration. Asserting their absence in the
		// chart source pins the migration so a future revert fails this test.
		expect(SEVEN_DAY_SRC).not.toMatch(/dayCountTooltipSingular/);
		expect(SEVEN_DAY_SRC).not.toMatch(/dayCountTooltipPlural/);
		// The whole `dayCountTooltip` family is off this chart now — it
		// bakes the slot label into the string, which the x axis already
		// shows. (The keys stay in the catalogs: the i18n key-contract
		// test uses `analytics.dayCountTooltip` as its plural-base
		// example, so deleting them would break that test's compile.)
		expect(SEVEN_DAY_SRC).not.toMatch(/analytics\.dayCountTooltip/);
		// The manual `day.count === 1` ternary is gone too (replaced by
		// Intl.PluralRules inside tChoice).
		expect(SEVEN_DAY_SRC).not.toMatch(/day\.count\s*===\s*1\s*\?/);
	});
});

describe("SevenDayActivityChart count tooltip (no permanent labels)", () => {
	it("drops the native title now that a custom tooltip follows the cursor", () => {
		// The mark used to carry `title={tooltip}`. A surviving `title`
		// would fire the browser's own tooltip on top of the custom one
		// — two tooltips for one hover — and jsdom cannot catch that, so
		// the absence is pinned at the source level.
		expect(SEVEN_DAY_SRC).not.toMatch(/title=\{tooltip\}/);
		expect(SEVEN_DAY_SRC).toMatch(/<TooltipBox/);
	});
});

describe("Corrections-applied card (server-side usage tracking)", () => {
	it("Dashboard.tsx renders the Corrections card in the merged card's second row", () => {
		// The Corrections cell lives in the merged card's second row
		// (Avg Speed / Longest session / Corrections), not as a loose
		// card below the chart. It reads the range-aware correction
		// totals from the hook and localises through the analytics.*
		// keys.
		expect(DASHBOARD_SRC).toMatch(/correctionStats/);
		expect(DASHBOARD_SRC).toMatch(/t\("analytics\.corrections"\)/);
		// The (?) tooltip and the "0% of dictations" rate sublabel are
		// both still gone: the cell is a bare count plus the trend every
		// cell in the row now carries.
		expect(DASHBOARD_SRC).not.toMatch(/t\("analytics\.correctionsTooltip"\)/);
		expect(DASHBOARD_SRC).not.toMatch(/t\("analytics\.correctionsRate"/);
		expect(DASHBOARD_SRC).toMatch(/correctionStats\.corrections/);
		expect(DASHBOARD_SRC).not.toMatch(/correctionStats\.rate/);
		// No cell in this page carries a sublabel line anymore.
		expect(DASHBOARD_SRC).not.toMatch(/sublabel=/);
		// CROSS-CARD CONSISTENCY: the trend was restored on this cell, so
		// it compares the window's corrections against the previous
		// window's rather than standing out as the one bare number.
		expect(DASHBOARD_SRC).toMatch(
			/computeTrend\(\s*correctionStats\.corrections,\s*correctionStats\.prevCorrections,?\s*\)/,
		);
		// The six cells sit in ONE merged card.
		expect(DASHBOARD_SRC).toMatch(/md:grid-cols-3/);
	});

	it("useDashboardData.ts fetches get_correction_usage in the Promise.all", () => {
		// The hook must fetch the usage snapshot alongside the other
		// dashboard data and derive the range-aware correction stats
		// from it (single fetch, consistent windows).
		// The fetch is multi-line (`call<...>(\n  "get_correction_usage",\n)`),
		// so match the bare command literal rather than an open-paren form.
		expect(HOOK_SRC).toMatch(/"get_correction_usage"/);
		expect(HOOK_SRC).toMatch(/computeCorrectionStats/);
		expect(HOOK_SRC).toMatch(/correctionStats/);
	});

	it("useDashboardData.ts derives model/device from install state via the SHARED helper", () => {
		// MODEL-STATE fix: the hook must only surface model/device
		// when the configured model's weights are actually on disk
		// (config defaults like "tiny"/"cuda" must not be advertised
		// as a live selection). The disk stat rides the shared
		// model-status snapshot (one flight with the Models page),
		// never a direct get_model_status IPC from this hook. The
		// `downloaded` check lives in ONE shared place,
		// resolveActiveModel in lib/utils/models.ts (the same helper
		// the About page uses) — never an inline duplicate.
		expect(HOOK_SRC).toMatch(/fetchSharedModelStatus\(/);
		expect(HOOK_SRC).not.toMatch(/"get_model_status"/);
		expect(HOOK_SRC).toMatch(/modelStatusMap/);
		expect(HOOK_SRC).toMatch(/resolveActiveModel\(/);
		// The install check itself is NOT in the hook anymore.
		expect(HOOK_SRC).not.toMatch(/downloaded === true/);
		// …it lives in the shared helper, which both the Analytics
		// data hook and the About page import.
		expect(MODELS_SRC).toMatch(/downloaded === true/);
	});
});

describe("Top stat cards: merged dictation card + range-aware values", () => {
	it("the single dictation card uses the plain totalDictations label", () => {
		// count) vs Card 3 "Total Dictations" (range-blind true
		// count), is merged into ONE card whose VALUE respects the
		// selected range. The LABEL is range-free: the
		// range control + the chart subtitle already state the
		// active window, and the suffixed label was the only one in
		// the row that truncated ("Total Dictations (7 D…").
		expect(DASHBOARD_SRC).toMatch(/analytics\.totalDictations/);
		// The suffixed label is gone from the source.
		expect(DASHBOARD_SRC).not.toMatch(/totalDictationsPeriod/);
	});

	it("the dictation value is range-aware and uncapped for All Time", () => {
		// Under "all", period.count is capped at the 500-row history
		// sample; the merged card falls back to the TRUE row count
		// (get_history_count) so it never shows a sample-capped
		// number while claiming to be the all-time total.
		expect(DASHBOARD_SRC).toMatch(
			/range === "all"\s*\?\s*String\(d\.totalCount\)\s*:\s*String\(period\.count\)/,
		);
	});

	it("no top stat card keeps a (?) tooltip (the range is in the segmented control)", () => {
		// The tooltips merely restated the selected range, removed
		expect(DASHBOARD_SRC).not.toMatch(/totalDictationsTooltip/);
		expect(DASHBOARD_SRC).not.toMatch(/activeDaysTooltip/);
	});

	it("StatCard no longer renders the (?) tooltip trigger", () => {
		const statCardSrc = fs.readFileSync(
			path.resolve(
				__dirname,
				"..",
				"..",
				"components",
				"dashboard",
				"StatCard.tsx",
			),
			"utf8",
		);
		expect(statCardSrc).not.toMatch(/infoTooltipAria/);
		expect(statCardSrc).not.toMatch(/CircleQuestionMarkIcon/);
		expect(statCardSrc).not.toMatch(/Tooltip/);
	});

	it("the Words card reuses the Home StatCards compact formatter", () => {
		// The Analytics Words card reuses the Home page Words
		// card's K-abbreviation formatting (exported from StatCards),
		// never a reimplementation; the value is the exact period word
		// count summed upstream, not a re-count of preview text.
		expect(DASHBOARD_SRC).toMatch(
			/import\s*\{[^}]*formatCompactNumber[^}]*\}\s*from\s*"@\/components\/dashboard\/StatCards"/,
		);
		expect(DASHBOARD_SRC).toMatch(
			/value=\{formatCompactNumber\(period\.wordCount\)\}/,
		);
	});

	it("the six cells are ONE merged card, cells divided not spaced", () => {
		// Row 1: Total Dictations / Recording Time / Words. Row 2:
		// Average Speed / Longest session / Corrections. All six live in
		// a single bordered surface. The cells carry no gap (the
		// container owns the radius/border/background and the dividers
		// do the separating), which is the merged-card UX contract.
		expect(DASHBOARD_SRC).toMatch(/md:grid-cols-3/);
		expect(DASHBOARD_SRC).toMatch(/grid-cols-1 divide-y divide-border\/8/);
		expect(DASHBOARD_SRC).toMatch(/md:divide-x md:divide-y-0/);
		expect(DASHBOARD_SRC).toMatch(
			/overflow-hidden rounded-lg border border-border\/8 bg-surface-subtle/,
		);
		// Every cell hands its chrome to that container.
		expect(DASHBOARD_SRC.match(/\binGroup\b/g)?.length).toBe(6);
		// …and every cell carries a trend, so no cell reads as a
		// different kind of thing (the cross-card consistency rule).
		// Longest Session and Corrections were the two that had none.
		expect(DASHBOARD_SRC.match(/\btrend=\{/g)?.length).toBe(6);
		// No gapped grid survives in the top row.
		expect(DASHBOARD_SRC).not.toMatch(/grid-cols-2 gap-3 md:grid-cols-4/);
	});

	it("drops the Active Days card and hands its figures to the heatmap", () => {
		// The Active Days card measured the heatmap's whole-year window,
		// not the selected range, so it is gone from the stat row and its
		// numbers are rendered by the heatmap header instead.
		expect(DASHBOARD_SRC).not.toMatch(/Calendar01Icon/);
		expect(DASHBOARD_SRC).not.toMatch(/analytics\.activeDays/);
		expect(DASHBOARD_SRC).toMatch(
			/<ActivityHeatmap heatmap=\{heatmap\} currentStreak=\{d\.currentStreak\} \/>/,
		);
		// The streak line survives — as the heatmap's, not a card's.
		expect(DASHBOARD_SRC).not.toMatch(/analytics\.noStreak/);
	});

	it("Longest Session uses a stopwatch icon, distinct from Recording Time's clock", () => {
		// Session now uses StopWatchIcon so the two durations are
		// visually distinguishable at a glance.
		expect(DASHBOARD_SRC).toContain("StopWatchIcon");
		expect(DASHBOARD_SRC).toMatch(
			/icon=\{StopWatchIcon\}[\s\S]*?analytics\.longestLabel/,
		);
	});
});

describe("Analytics polish: stat-card spacing, sublabel pruning, header chrome", () => {
	it("Recording Time card no longer renders the 'avg per dictation' sublabel", () => {
		// POLISH: the "avg 1m each" line added no useful information
		expect(DASHBOARD_SRC).not.toMatch(/analytics\.avgPerDictation/);
		// The card itself survives with its duration value + trend.
		expect(DASHBOARD_SRC).toMatch(/analytics\.recordingTime/);
		expect(DASHBOARD_SRC).toMatch(
			/value=\{formatDuration\(period\.duration\)\}/,
		);
	});

	it("the streak line lives on the heatmap, still hidden when there is none", () => {
		// POLISH: the empty-streak sublabel never existed; a real streak
		// renders its "{count}-day streak" line. Both figures moved to
		// the heatmap header, so the guard lives there now.
		const heatmapSrc = fs.readFileSync(
			path.resolve(
				__dirname,
				"..",
				"dashboard",
				"components",
				"ActivityHeatmap.tsx",
			),
			"utf8",
		);
		expect(heatmapSrc).toMatch(/analytics\.dayStreak/);
		expect(heatmapSrc).toMatch(
			/currentStreak\s*>\s*0\s*&&[\s\S]*?t\("analytics\.dayStreak"/,
		);
		// The figure is the heatmap's OWN window, never the range-aware
		// period count: it has to come off the `heatmap` object the page
		// hands the card, and the card must not reach for `period` at all.
		expect(heatmapSrc).toMatch(/\{[^}]*\bactiveDays\b[^}]*\}\s*=\s*heatmap/);
		expect(heatmapSrc).not.toMatch(/period\./);
		expect(heatmapSrc).not.toMatch(/analytics\.noStreak/);
	});

	it("stat cards push the value down with an auto top margin (breathing room)", () => {
		// POLISH: the value+trend row's `mt-auto` pins the icon+label row
		// to the top of the stretched cell and pushes the number to the
		// bottom.
		const statCardSrc = fs.readFileSync(
			path.resolve(
				__dirname,
				"..",
				"..",
				"components",
				"dashboard",
				"StatCard.tsx",
			),
			"utf8",
		);
		expect(statCardSrc).toMatch(/mt-auto flex items-baseline justify-between/);
		expect(statCardSrc).toMatch(/text-2xl font-semibold/);
		// POLISH round 2: the cell carries a minimum height so the
		// auto-top-margin actually has room to spread (a cell only as
		// tall as its content leaves no breathing space).
		expect(statCardSrc).toMatch(/min-h-24/);
		// The moved derived-metric cells (Longest session / Corrections)
		// are StatCards now, so they share this exact rhythm.
	});

	it("neither analytics card draws a header icon any more", () => {
		// Both card headers are title + range line only. The Activity
		// card's glyph and the heatmap's grid glyph were decoration
		// beside an already-explicit heading, so no icon is imported
		// (or rendered) by either card.
		const heatmapSrc = fs.readFileSync(
			path.resolve(
				__dirname,
				"..",
				"dashboard",
				"components",
				"ActivityHeatmap.tsx",
			),
			"utf8",
		);
		for (const src of [SEVEN_DAY_SRC, heatmapSrc]) {
			expect(src).not.toMatch(/HugeiconsIcon/);
			expect(src).not.toMatch(/@hugeicons\/core-free-icons/);
		}
	});
});

describe("Range + refresh controls live outside the scrolling body", () => {
	it("the page renders no range control of its own", () => {
		// The range selector moved into the app title bar, so the page
		// must not keep a second copy (two controls on one store would
		// look like duplicated chrome and scroll out of reach), and it
		// must not keep its own setter for the value either.
		expect(DASHBOARD_SRC).not.toMatch(/TimeRangeSelector/);
		expect(DASHBOARD_SRC).not.toMatch(/setRange/);
	});

	it("the title bar mounts the range control for the analytics page", () => {
		const titleBarSrc = fs.readFileSync(
			path.resolve(
				__dirname,
				"..",
				"..",
				"components",
				"layout",
				"TitleBar.tsx",
			),
			"utf8",
		);
		expect(titleBarSrc).toMatch(
			/<AnalyticsRangeSwitcher currentPage=\{currentPage\} \/>/,
		);
	});

	it("the hook reads the range from the shared store, not local state", () => {
		// The control lives in a sibling tree, so the value has to come
		// from somewhere both trees can see. A `useState` here would
		// silently freeze the page at the store's first value.
		expect(HOOK_SRC).toMatch(/useAnalyticsRange/);
		expect(HOOK_SRC).not.toMatch(/useState<RangeId>/);
		expect(HOOK_SRC).not.toMatch(/setRange/);
	});

	it("the refresh indicator sits in the PageHeading action row beside Share", () => {
		// Both are page-level actions, so they share the heading's
		// right-hand row instead of a row of their own. Their ORDER in that
		// row is not the contract — only that the heading owns both — so
		// this asserts membership inside the PageHeading element rather
		// than a fixed sequence. The LAST heading is the loaded view's
		// (the first-load heading above it mounts Share only, C-CACHE-6,
		// keeping zero live regions while data is absent).
		const headings = [
			...DASHBOARD_SRC.matchAll(/<PageHeading[\s\S]*?<\/PageHeading>/g),
		].map((m) => m[0]);
		const heading = headings[headings.length - 1];

		expect(heading).toBeTruthy();
		expect(heading).toContain("<ShareStatsDialog");
		expect(heading).toContain("<LastUpdatedIndicator");
		// …and the old standalone row is gone.
		expect(DASHBOARD_SRC).not.toMatch(
			/flex flex-wrap items-center justify-between gap-3 pb-2/,
		);
	});

	it("the skeleton mirrors the heading actions and reserves no range row", () => {
		const skeletonSrc = fs.readFileSync(
			path.resolve(
				__dirname,
				"..",
				"dashboard",
				"components",
				"DashboardSkeleton.tsx",
			),
			"utf8",
		);
		expect(skeletonSrc).toMatch(/<HeadingSkeleton[\s\S]*?action=\{/);
		// The pill row is what the old range skeleton looked like; the
		// control is in the title bar now, so nothing pill-shaped is
		// left to reserve space for.
		expect(skeletonSrc).not.toMatch(/rounded-full/);
	});
});

describe("Analytics dynamic-height animator (C-ANIM-1)", () => {
	it("sampled-window footnote rides the collapse-root animator", () => {
		// The footnote arrives once the custom fetch lands (after
		// first paint). The wrapper stays mounted for the whole
		// custom range and only the data-open flag toggles, so the
		// line fades/slides open instead of jumping the heatmap down.
		expect(DASHBOARD_SRC).toMatch(/collapse-root/);
		expect(DASHBOARD_SRC).toMatch(/data-open=\{customCapped\}/);
	});

	it("collapse-root + mount-fade utilities exist with their own reduce overrides", () => {
		const indexCss = fs.readFileSync(
			path.resolve(__dirname, "..", "..", "index.css"),
			"utf8",
		);
		expect(indexCss).toMatch(/\.collapse-root/);
		expect(indexCss).toMatch(/grid-template-rows: 0fr/);
		expect(indexCss).toMatch(/grid-template-rows: 1fr/);
		expect(indexCss).toMatch(/@keyframes mountFade/);
		expect(indexCss).toMatch(/\.mount-fade/);
		// The app-wide `*` kill-switch loses to class specificity
		// (0,1,0 beats 0,0,0), so the utilities carry their own
		// reduce override placed AFTER the base for the tie-break.
		const baseIdx = indexCss.indexOf(".collapse-root");
		const reduceIdx = indexCss.indexOf(
			"@media (prefers-reduced-motion: reduce)",
			baseIdx,
		);
		expect(reduceIdx).toBeGreaterThan(baseIdx);
		const reduceBlock = indexCss.slice(reduceIdx);
		expect(reduceBlock).toMatch(/\.collapse-root/);
		expect(reduceBlock).toMatch(/\.mount-fade/);
	});
});
