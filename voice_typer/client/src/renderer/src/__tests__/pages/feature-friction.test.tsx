/**
 * W1-A4 / XA-5 feature-friction regression suite.
 *
 * Verifies the specific XA-5 fixes covered by this suite:
 *
 *   • XA-5-6, the Cancel-download button is wrapped in a
 *     ``ConfirmDialog`` with ``variant="destructive"``. A single
 *     stray click must NOT immediately invoke ``onCancel``, it must
 *     open the confirmation dialog, and only the dialog's "confirm"
 *     action triggers ``onCancel``.
 *   • XA-5-16, the ``models.download.oneAtATime`` key exists in ALL
 *     8 locale files; the ``ModelCardActions`` source no longer
 *     contains a hardcoded English-literal fallback (the catalogue
 *     is the single source of truth).
 *   • XA-5-7, the inline ``Retry`` button renders on the
 *     ``DownloadProgressBar`` when (error + onRetry) are both
 *     provided (already covered by the canonical DownloadProgressBar
 *     suite; re-asserted here from the W1-A4 perspective).
 *   • XA-5-12, the preset selector keeps the primary "improve your
 *     mic" control OUTSIDE any disclosure: the collapsed selector
 *     header (label + current selection) renders without expanding.
 *     Re-pointed at the LIVE Microphone-page surface
 *     (``PresetAccordionSelector``), the former dropdown variant
 *     (``AudioPresetSelector``) was production-dead and has been
 *     deleted (both live surfaces now share the preset data registry
 *     ``lib/utils/audioPresets.ts``).
 *
 * Tests run on LINUX (sandbox). They render real React components
 * (DownloadProgressBar, PresetAccordionSelector) and read the source
 * files + locale catalogues for the structural assertions that don't
 * warrant a full RTL mount (ModelCardActions source scan + locale
 * parity + the dead-component regression guard).
 */

import * as fs from "node:fs";
import * as path from "node:path";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { createRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ar from "@/i18n/translations/ar.json";
import de from "@/i18n/translations/de.json";
import en from "@/i18n/translations/en.json";
import es from "@/i18n/translations/es.json";
import fr from "@/i18n/translations/fr.json";
import hi from "@/i18n/translations/hi.json";
import ru from "@/i18n/translations/ru.json";
import zh from "@/i18n/translations/zh.json";

// Stub `t()` so the rendered labels are deterministic sentinels we can
// assert on without depending on the catalogue's copy text. Use
// `importOriginal` so the rest of the i18n module (getLocale, setLocale,
// useT, etc.) keeps its real implementation, DownloadProgressBar pulls
// `formatBytes` → `lib/format` → `getLocale` from the same module.
vi.mock("@/i18n/i18n", async (importOriginal) => {
	const actual = await importOriginal<typeof import("@/i18n/i18n")>();
	return {
		...actual,
		t: (key: string, params?: Record<string, string>) => {
			const paramStr = params ? `:${JSON.stringify(params)}` : "";
			return `[t]${key}${paramStr}`;
		},
	};
});

// Stub InfoTooltip so PresetAccordionSelector mounts without the
// app-global TooltipProvider boundary (the real component mounts a
// Radix Tooltip.Root). The XA-5-12 cases below only assert the
// selector's header/disclosure structure, not tooltip behaviour —
// that is pinned in __tests__/microphone-a11y.test.tsx with a
// faithful trigger-contract mock.
vi.mock("@/components/feedback/InfoTooltip", () => ({
	InfoTooltip: ({ text }: { text: string }) => (
		<span data-testid="info-tooltip" data-text={text} />
	),
}));

// The former per-page VocabToolbar mirror was replaced by the shared
// CollectionToolbar shell (the page injects its label keys); the XA-5-15
// sort-in-toolbar contract now pins the shared shell wired with the
// Vocabulary page's keys, exactly what pages/Vocabulary.tsx renders.
import { CollectionToolbar } from "@/components/common/CollectionToolbar";
import { TestReviewPanel } from "@/components/microphone/TestReviewPanel";
import { DownloadProgressBar } from "@/components/models/DownloadProgressBar";
import { PresetAccordionSelector } from "@/pages/microphone/components/PresetAccordionSelector";

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

const RENDERER_SRC_ROOT = path.join(__dirname, "..", "..");

const baseProps = {
	progress: 50,
	status: "downloading",
	isPaused: false,
	downloadedBytes: 1024 * 500,
	totalBytes: 1024 * 1024,
	speedBps: 1024 * 100,
	etaSeconds: 60,
	onTogglePause: vi.fn(),
	onCancel: vi.fn(),
};

describe("XA-5-6, Cancel-download is wrapped in ConfirmDialog", () => {
	afterEach(() => {
		cleanup();
	});

	beforeEach(() => {
		vi.clearAllMocks();
	});

	it("a stray click on Cancel does NOT immediately invoke onCancel", () => {
		const onCancel = vi.fn();
		render(<DownloadProgressBar {...baseProps} onCancel={onCancel} />);
		const cancelBtn = screen.getByRole("button", {
			name: /\[t\]models\.download\.cancelAria/,
		});
		fireEvent.click(cancelBtn);
		// The ConfirmDialog opens instead, onCancel must NOT fire yet.
		expect(onCancel).not.toHaveBeenCalled();
	});

	it("opens the ConfirmDialog on Cancel click (destructive title + message visible)", () => {
		render(<DownloadProgressBar {...baseProps} onCancel={vi.fn()} />);
		fireEvent.click(
			screen.getByRole("button", {
				name: /\[t\]models\.download\.cancelAria/,
			}),
		);
		// The dialog title + message render as live text, they come
		// from the new `models.download.cancelConfirmTitle` /
		// `cancelConfirmMessage` / `cancelConfirmAction` i18n keys.
		expect(
			screen.getByText(/\[t\]models\.download\.cancelConfirmTitle/),
		).toBeInTheDocument();
		expect(
			screen.getByText(/\[t\]models\.download\.cancelConfirmMessage/),
		).toBeInTheDocument();
	});

	it("confirming the dialog invokes onCancel exactly once", () => {
		const onCancel = vi.fn();
		render(<DownloadProgressBar {...baseProps} onCancel={onCancel} />);
		fireEvent.click(
			screen.getByRole("button", {
				name: /\[t\]models\.download\.cancelAria/,
			}),
		);
		// Click the destructive confirm button (its label is the
		// `cancelConfirmAction` translation).
		const confirmBtn = screen.getByRole("button", {
			name: /\[t\]models\.download\.cancelConfirmAction/,
		});
		fireEvent.click(confirmBtn);
		expect(onCancel).toHaveBeenCalledTimes(1);
	});
});

describe("XA-5-7, inline Retry button on failed download", () => {
	afterEach(() => {
		cleanup();
	});

	beforeEach(() => {
		vi.clearAllMocks();
	});

	it("renders a Retry button when (error + onRetry) are both provided", () => {
		render(
			<DownloadProgressBar
				{...baseProps}
				error="disk full"
				onRetry={vi.fn()}
			/>,
		);
		expect(
			screen.getByRole("button", { name: /\[t\]models\.download\.retryAria/ }),
		).toBeInTheDocument();
	});

	it("clicking the Retry button invokes onRetry exactly once", () => {
		const onRetry = vi.fn();
		render(
			<DownloadProgressBar
				{...baseProps}
				error="network timeout"
				onRetry={onRetry}
			/>,
		);
		fireEvent.click(
			screen.getByRole("button", { name: /\[t\]models\.download\.retryAria/ }),
		);
		expect(onRetry).toHaveBeenCalledTimes(1);
	});
});

describe("XA-5-16, `models.download.oneAtATime` locale parity", () => {
	const KEY = "models.download.oneAtATime";
	it.each(Object.keys(LOCALES))("locale `%s` contains the key", (locale) => {
		expect(hasKey(LOCALES[locale], KEY)).toBe(true);
	});

	it("ModelCardActions no longer hardcodes an English fallback for the tooltip", () => {
		const src = fs.readFileSync(
			path.join(
				RENDERER_SRC_ROOT,
				"components",
				"models",
				"ModelCardActions.tsx",
			),
			"utf8",
		);
		// The catalogue is the single source of truth, `oneAtATimeTitle`
		// calls `t("models.download.oneAtATime")` directly with no
		// fallback. A literal English string in this function (e.g.
		// `return "Only one download at a time"`) would be a regression.
		expect(src).toMatch(/oneAtATimeTitle\(\)/);
		expect(src).toMatch(/t\("models\.download\.oneAtATime"\)/);
		expect(src).not.toMatch(/Only one download at a time[^"]*"[^)]*\)/);
	});
});

describe("XA-5-6, cancel-confirm locale keys exist in ALL 8 locale files", () => {
	const KEYS = [
		"models.download.cancelConfirmTitle",
		"models.download.cancelConfirmMessage",
		"models.download.cancelConfirmAction",
	] as const;
	it.each(KEYS)("locale catalogue contains `%s`", (key) => {
		const missing: string[] = [];
		for (const [locale, catalogue] of Object.entries(LOCALES)) {
			if (!hasKey(catalogue, key)) missing.push(locale);
		}
		expect(missing).toEqual([]);
	});
});

describe("XA-5-12, preset selector keeps the primary CTA outside any disclosure", () => {
	afterEach(() => {
		cleanup();
	});

	it("renders the collapsed selector header (label + current preset) without expanding", () => {
		render(
			<PresetAccordionSelector
				preset="auto"
				config={{} as never}
				showAdvanced={false}
				onPresetChange={vi.fn()}
				onToggleAdvanced={vi.fn()}
				onConfigChange={vi.fn()}
			/>,
		);
		// The collapsed header always shows the section label, the
		// primary "improve your mic" control is visible without
		// expanding anything (the same friction guarantee the old
		// always-visible Select provided).
		const trigger = screen.getByRole("button", {
			name: /\[t\]settings\.audioEnhancement\.microphoneQuality/,
		});
		expect(trigger).toBeInTheDocument();
		expect(trigger.getAttribute("aria-expanded")).toBe("false");

		// The current selection chip sits in the collapsed header, so the
		// active preset is readable without expanding either.
		expect(screen.getByTestId("mic-preset-current").textContent).toBe(
			"[t]settings.audioEnhancement.presetAuto",
		);
	});

	it("reveals the Custom Filters disclosure only under the custom preset", () => {
		const { rerender } = render(
			<PresetAccordionSelector
				preset="auto"
				config={{} as never}
				showAdvanced={false}
				onPresetChange={vi.fn()}
				onToggleAdvanced={vi.fn()}
				onConfigChange={vi.fn()}
			/>,
		);
		// Expand the accordion so the option list is mounted.
		fireEvent.click(screen.getByRole("button", { expanded: false }));

		// Non-custom preset → no Custom-filters toggle (nothing to
		// reveal).
		expect(
			screen.queryByText(/\[t\]settings\.audioEnhancement\.customFiltersTitle/),
		).toBeNull();

		rerender(
			<PresetAccordionSelector
				preset="custom"
				config={{} as never}
				showAdvanced={false}
				onPresetChange={vi.fn()}
				onToggleAdvanced={vi.fn()}
				onConfigChange={vi.fn()}
			/>,
		);
		// Custom preset → the disclosure toggle is now visible inside
		// the expanded region.
		expect(
			screen.getByText(/\[t\]settings\.audioEnhancement\.customFiltersTitle/),
		).toBeInTheDocument();
	});
});

// ─────────────────────────────────────────────────────────────────────
// W3-A6 / XA-5 friction-items continuation.
//
// Verifies the additional XA-5 items implemented in the follow-up batch:
//   • XA-5-4, useFilterState persists values across re-mounts via
//     sessionStorage.
//   • XA-5-8, TestReviewPanel renders a recommendation block per
//     detected issue (text always; one-click CTA when onApplyPreset
//     is wired).
//   • XA-5-11, CloudProvidersPanel renders an eye-icon show/hide
//     toggle next to the API key input.
//   • XA-5-13, useMicrophoneTestSession exposes a module-level cache
//     reset helper (the cache itself is exercised indirectly via the
//     session hook's stop/start/selectMicrophone paths).
//   • XA-5-15, VocabToolbar renders the sort control in its single
//     toolbar row (no count badge, no orphaned second row).
//   • XA-5-17, Models page computes an ``activeModelSummary`` from
//     the lifecycle.config (verified by source scan, mounting the
//     full page requires too many mock dependencies for a focused
//     unit test).
//   • XA-5-20, Import buttons carry a ``title`` attribute pointing
//     at the importFormatHint i18n key.
//
// Tests run on LINUX (sandbox).
// ─────────────────────────────────────────────────────────────────────

describe("XA-5-4, useFilterState persists values across re-mounts", () => {
	beforeEach(() => {
		sessionStorage.clear();
	});

	it("returns the initial value on first mount (no prior session)", async () => {
		const { renderHook } = await import("@testing-library/react");
		const { useFilterState } = await import("@/hooks/useFilterState");
		const { result } = renderHook(() =>
			useFilterState("testPage", "query", "initial"),
		);
		expect(result.current[0]).toBe("initial");
	});

	it("a new mount in the same session reads the persisted value", async () => {
		const { renderHook } = await import("@testing-library/react");
		const { useFilterState } = await import("@/hooks/useFilterState");
		sessionStorage.setItem(
			"vt:filters:otherPage.sortOrder",
			JSON.stringify("oldest"),
		);
		const { result } = renderHook(() =>
			useFilterState("otherPage", "sortOrder", "newest"),
		);
		// Persisted value wins over the initial value.
		expect(result.current[0]).toBe("oldest");
	});

	it("setter writes the next value through to sessionStorage", async () => {
		const { renderHook, act } = await import("@testing-library/react");
		const { useFilterState } = await import("@/hooks/useFilterState");
		const { result } = renderHook(() =>
			useFilterState("setterPage", "tab", "local"),
		);
		act(() => result.current[1]("cloud"));
		expect(result.current[0]).toBe("cloud");
		expect(sessionStorage.getItem("vt:filters:setterPage.tab")).toBe(
			JSON.stringify("cloud"),
		);
	});
});

describe("XA-5-8, TestReviewPanel renders per-issue recommendations", () => {
	afterEach(() => {
		cleanup();
	});

	const basePanelProps = {
		durationMs: 5000,
		testAudioBase64: "data:audio/wav;base64,AAAA",
		rawAudioBase64: null,
		playing: false,
		playingOriginal: false,
		onPlayEnhanced: vi.fn(),
		onPlayOriginal: vi.fn(),
		onStop: vi.fn(),
		onRetest: vi.fn(),
		hasFiltersEnabled: false,
	};

	const qualityWithIssues = {
		volume_level: "good" as const,
		volume_rms: 0.5,
		peak_level: 0.7,
		noise_level: "high" as const,
		has_voice: true,
		has_clipping: false,
		detected_issues: ["High background noise"],
		estimated_transcription_quality: 60,
		silence_ratio: 0.1,
	};

	it("renders the recommendation text for a detected issue", () => {
		render(<TestReviewPanel {...basePanelProps} quality={qualityWithIssues} />);
		// The recommendation text is rendered inside a
		// data-testid="issue-recommendation" element so a future
		// refactor that drops the recommendation surfaces
		// immediately.
		const rec = screen.getByTestId("issue-recommendation");
		expect(rec.textContent).toMatch(
			/\[t\]microphoneTest\.recommendations\.high_noise/,
		);
	});

	it("renders the one-click Apply-preset CTA when onApplyPreset is wired", () => {
		const onApplyPreset = vi.fn();
		render(
			<TestReviewPanel
				{...basePanelProps}
				quality={qualityWithIssues}
				onApplyPreset={onApplyPreset}
				currentPreset="auto"
			/>,
		);
		const cta = screen.getByTestId("issue-apply-preset");
		fireEvent.click(cta);
		expect(onApplyPreset).toHaveBeenCalledWith("noisy_room");
	});

	it("does NOT render the Apply-preset CTA when currentPreset already matches", () => {
		render(
			<TestReviewPanel
				{...basePanelProps}
				quality={qualityWithIssues}
				onApplyPreset={vi.fn()}
				currentPreset="noisy_room"
			/>,
		);
		expect(screen.queryByTestId("issue-apply-preset")).toBeNull();
	});
});

describe("XA-5-11, CloudProvidersPanel renders the API-key eye toggle", () => {
	it("CloudProvidersPanel source wires the eye-icon show/hide toggle", () => {
		// Source-scan assertion: the ProviderConfigForm sub-component
		// declares a ``revealKey`` state + a button that flips it +
		// the input ``type`` is bound to that state. Mounting the
		// panel for a behavioral test would require expanding the
		// Radix Accordion group first (the Configure button sits
		// inside AccordionContent, which Radix defers); the
		// source-scan covers the wiring without that orchestration.
		const src = fs.readFileSync(
			path.join(
				RENDERER_SRC_ROOT,
				"components",
				"models",
				"CloudProvidersPanel.tsx",
			),
			"utf8",
		);
		expect(src).toMatch(
			/const \[revealKey, setRevealKey\] = useState\(false\)/,
		);
		expect(src).toMatch(/type=\{revealKey \? "text" : "password"\}/);
		expect(src).toMatch(/t\("models\.cloud\.apiKeyShowAria"/);
		expect(src).toMatch(/t\("models\.cloud\.apiKeyHideAria"/);
		expect(src).toMatch(/t\("models\.cloud\.apiKeyFormatHint"/);
	});
});

describe("XA-5-13, useMicrophoneTestSession exposes a cache-reset helper", () => {
	it("exports _resetMicrophoneTestCache as a function", async () => {
		const mod = await import(
			"@/pages/microphone/hooks/useMicrophoneTestSession"
		);
		expect(typeof mod._resetMicrophoneTestCache).toBe("function");
	});
});

describe("XA-5-15, the collection toolbar keeps the sort control in the single toolbar row", () => {
	afterEach(() => {
		cleanup();
	});

	it("renders the sort Select inside the toolbar, no count badge", () => {
		render(
			<CollectionToolbar
				importInputRef={createRef<HTMLInputElement>()}
				importAccept="application/json,.json,.csv,text/csv"
				onImportClick={vi.fn()}
				onImportFile={vi.fn()}
				importAriaLabelKey="common.importAria"
				importLabelKey="common.import"
				importTitleKey="vocabulary.importFormatHint"
				onExport={vi.fn()}
				exportDisabled={false}
				onClearAll={vi.fn()}
				clearAllDisabled={false}
				clearAllAriaLabelKey="vocabulary.clearAllAria"
				clearAllLabelKey="vocabulary.clearAll"
				addAriaLabelKey="vocabulary.addNewAria"
				addLabelKey="vocabulary.addWord"
				addDisabled={false}
				onAdd={vi.fn()}
				sortOrder="newest"
				onSortOrderChange={vi.fn()}
				hasEntries
			/>,
		);
		// The sort control lives in the toolbar's secondary cluster
		// (single-row toolbar, no orphaned second row). The i18n mock
		// resolves aria-label to "[t]common.sortAria".
		expect(screen.getByRole("combobox")).toBeTruthy();
		expect(screen.queryByTestId("vocab-entry-count-badge")).toBeNull();
	});
});

describe("XA-5-17, Models page computes an active-model summary", () => {
	it("source does NOT render the active-model summary banner (removed per user decision)", () => {
		const src = fs.readFileSync(
			path.join(RENDERER_SRC_ROOT, "pages", "Models.tsx"),
			"utf8",
		);
		// The banner was removed: the active model is already indicated by
		// the selected card's button state, so the extra highlighted
		// summary banner was redundant for every module. Pin its absence —
		// a regression that re-adds it would reintroduce the duplication.
		expect(src).not.toMatch(/data-testid="models-active-model-summary"/);
		expect(src).not.toMatch(/activeModelSummary\s*=\s*useMemo/);
		expect(src).not.toMatch(/t\("models\.activeModelSummaryLabel"\)/);
	});
});

describe("XA-5-20, Import buttons carry a format-hint title attribute", () => {
	it("Vocabulary page wires the importFormatHint key into the shared toolbar's Import title", () => {
		const src = fs.readFileSync(
			path.join(RENDERER_SRC_ROOT, "pages", "Vocabulary.tsx"),
			"utf8",
		);
		// The title attr renders inside the shared CollectionToolbar shell;
		// the KEY is injected by the page (the shell test pins the
		// key→title wiring, this pins the page passes the right key).
		expect(src).toMatch(/importTitleKey="vocabulary\.importFormatHint"/);
	});

	it("Templates page wires the importFormatHint key into the shared toolbar's Import title", () => {
		const src = fs.readFileSync(
			path.join(RENDERER_SRC_ROOT, "pages", "Templates.tsx"),
			"utf8",
		);
		expect(src).toMatch(/importTitleKey="templates\.importFormatHint"/);
	});
});

describe("XA-5 locale parity for the new keys", () => {
	const NEW_KEYS = [
		"microphoneTest.recommendations.high_noise",
		"microphoneTest.recommendations.moderate_noise",
		"microphoneTest.recommendations.clipping",
		"microphoneTest.recommendations.volume_too_low",
		"microphoneTest.recommendations.volume_low",
		"microphoneTest.recommendations.no_voice",
		"microphoneTest.recommendations.applyNoisyRoom",
		"models.cloud.apiKeyShowAria",
		"models.cloud.apiKeyHideAria",
		"models.cloud.apiKeyFormatHint",
		"vocabulary.count_zero",
		"vocabulary.count_one",
		"vocabulary.count_two",
		"vocabulary.count_few",
		"vocabulary.count_many",
		"vocabulary.count_other",
		"vocabulary.importFormatHint",
		"templates.importFormatHint",
	] as const;

	it.each(NEW_KEYS)("locale catalogue contains `%s`", (key) => {
		const missing: string[] = [];
		for (const [locale, catalogue] of Object.entries(LOCALES)) {
			if (!hasKey(catalogue, key)) missing.push(locale);
		}
		expect(missing, `missing in locales: ${missing.join(", ")}`).toEqual([]);
	});
});

// ─────────────────────────────────────────────────────────────────────
// Preset-surface consolidation regression guard.
//
// The 5-preset microphone-quality surface was forked three ways: two
// live presentations (the Settings → Audio Select and the Microphone
// page's PresetAccordionSelector) plus a production-DEAD dropdown
// variant (components/microphone/AudioPresetSelector.tsx, zero render
// sites outside this suite's own renders, kept compiling only by the
// `AudioPreset` type imports). The dead file was deleted and both live
// surfaces now share the preset data registry
// (lib/utils/audioPresets.ts). These guards pin that state: the file
// stays gone, no non-test source references it, and the shared
// registry is what the live surfaces consume.
// ─────────────────────────────────────────────────────────────────────
describe("dead preset dropdown stays deleted (one shared preset-data source)", () => {
	const DEAD_COMPONENT_PATH = path.join(
		RENDERER_SRC_ROOT,
		"components",
		"microphone",
		"AudioPresetSelector.tsx",
	);

	it("the dead component file does not exist on disk", () => {
		expect(fs.existsSync(DEAD_COMPONENT_PATH)).toBe(false);
	});

	it("no non-test renderer source references AudioPresetSelector", () => {
		const offenders: string[] = [];
		const visit = (dir: string) => {
			for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
				const full = path.join(dir, entry.name);
				if (entry.isDirectory()) {
					visit(full);
					continue;
				}
				if (!/\.(ts|tsx)$/.test(entry.name)) continue;
				// Tests (and Storybook fixtures) may name the deleted
				// component while documenting the guard itself, only
				// production source is forbidden from referencing it.
				if (entry.name.includes(".test.") || entry.name.includes(".spec.")) {
					continue;
				}
				if (full.includes("__tests__") || full.includes(".stories.")) continue;
				const src = fs.readFileSync(full, "utf8");
				if (src.includes("AudioPresetSelector")) offenders.push(full);
			}
		};
		visit(RENDERER_SRC_ROOT);
		expect(offenders).toEqual([]);
	});

	it("the live surfaces import the shared preset data registry", () => {
		const settingsSrc = fs.readFileSync(
			path.join(
				RENDERER_SRC_ROOT,
				"components",
				"settings",
				"AudioSettingsSection.tsx",
			),
			"utf8",
		);
		const micPageSrc = fs.readFileSync(
			path.join(
				RENDERER_SRC_ROOT,
				"pages",
				"microphone",
				"components",
				"PresetAccordionSelector.tsx",
			),
			"utf8",
		);
		expect(settingsSrc).toMatch(
			/import \{ AUDIO_PRESET_OPTIONS \} from "@\/lib\/utils\/audioPresets"/,
		);
		expect(micPageSrc).toMatch(
			/import \{\n\tAUDIO_PRESET_OPTIONS,\n\ttype AudioPreset,\n\} from "@\/lib\/utils\/audioPresets"/,
		);
	});
});
