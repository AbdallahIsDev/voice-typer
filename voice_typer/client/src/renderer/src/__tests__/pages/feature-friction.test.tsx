/**
 * W1-A4 / XA-5 feature-friction regression suite.
 *
 * Verifies the specific XA-5 fixes the W1-A4 sub-agent owns:
 *
 *   • XA-5-6 — the Cancel-download button is wrapped in a
 *     ``ConfirmDialog`` with ``variant="destructive"``. A single
 *     stray click must NOT immediately invoke ``onCancel`` — it must
 *     open the confirmation dialog, and only the dialog's "confirm"
 *     action triggers ``onCancel``.
 *   • XA-5-16 — the ``models.download.oneAtATime`` key exists in ALL
 *     8 locale files; the ``ModelCardActions`` source no longer
 *     contains a hardcoded English-literal fallback (the catalogue
 *     is the single source of truth).
 *   • XA-5-7 — the inline ``Retry`` button renders on the
 *     ``DownloadProgressBar`` when (error + onRetry) are both
 *     provided (already covered by the canonical DownloadProgressBar
 *     suite; re-asserted here from the W1-A4 perspective).
 *   • XA-5-12 — ``AudioPresetSelector`` renders the preset Select
 *     OUTSIDE the collapsible (the primary "improve your mic" CTA is
 *     always visible).
 *
 * Tests run on LINUX (sandbox). They render real React components
 * (DownloadProgressBar) and read the source files + locale catalogues
 * for the structural assertions that don't warrant a full RTL mount
 * (ModelCardActions source scan + locale parity).
 */

import * as fs from "node:fs";
import * as path from "node:path";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
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
// useT, etc.) keeps its real implementation — DownloadProgressBar pulls
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

import { AudioPresetSelector } from "@/components/microphone/AudioPresetSelector";
import { TestReviewPanel } from "@/components/microphone/TestReviewPanel";
import { DownloadProgressBar } from "@/components/models/DownloadProgressBar";
import { VocabSearchFilterBar } from "@/pages/vocabulary/components/VocabSearchFilterBar";

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

describe("XA-5-6 — Cancel-download is wrapped in ConfirmDialog", () => {
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
		// The ConfirmDialog opens instead — onCancel must NOT fire yet.
		expect(onCancel).not.toHaveBeenCalled();
	});

	it("opens the ConfirmDialog on Cancel click (destructive title + message visible)", () => {
		render(<DownloadProgressBar {...baseProps} onCancel={vi.fn()} />);
		fireEvent.click(
			screen.getByRole("button", {
				name: /\[t\]models\.download\.cancelAria/,
			}),
		);
		// The dialog title + message render as live text — they come
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

describe("XA-5-7 — inline Retry button on failed download", () => {
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

describe("XA-5-16 — `models.download.oneAtATime` locale parity", () => {
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
		// The catalogue is the single source of truth — `oneAtATimeTitle`
		// calls `t("models.download.oneAtATime")` directly with no
		// fallback. A literal English string in this function (e.g.
		// `return "Only one download at a time"`) would be a regression.
		expect(src).toMatch(/oneAtATimeTitle\(\)/);
		expect(src).toMatch(/t\("models\.download\.oneAtATime"\)/);
		expect(src).not.toMatch(/Only one download at a time[^"]*"[^)]*\)/);
	});
});

describe("XA-5-6 — cancel-confirm locale keys exist in ALL 8 locale files", () => {
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

describe("XA-5-12 — AudioPresetSelector renders the preset Select outside the collapsible", () => {
	afterEach(() => {
		cleanup();
	});

	it("renders the preset combobox unconditionally (no expand required)", () => {
		render(
			<AudioPresetSelector
				preset="auto"
				config={{} as never}
				showAdvanced={false}
				onPresetChange={vi.fn()}
				onToggleAdvanced={vi.fn()}
				onConfigChange={vi.fn()}
			/>,
		);
		// The Select trigger's accessible name is the "microphone
		// quality preset" label — always present regardless of the
		// collapsible state.
		const trigger = screen.getByRole("combobox", {
			name: /\[t\]a11y\.microphoneQualityPreset/,
		});
		expect(trigger).toBeInTheDocument();
	});

	it("renders the Custom Filters collapse toggle only when preset === 'custom'", () => {
		const { rerender } = render(
			<AudioPresetSelector
				preset="auto"
				config={{} as never}
				showAdvanced={false}
				onPresetChange={vi.fn()}
				onToggleAdvanced={vi.fn()}
				onConfigChange={vi.fn()}
			/>,
		);
		// Non-custom preset → no collapse toggle (nothing to reveal).
		expect(
			screen.queryByText(/\[t\]settings\.audioEnhancement\.customFiltersTitle/),
		).toBeNull();

		rerender(
			<AudioPresetSelector
				preset="custom"
				config={{} as never}
				showAdvanced={false}
				onPresetChange={vi.fn()}
				onToggleAdvanced={vi.fn()}
				onConfigChange={vi.fn()}
			/>,
		);
		// Custom preset → the collapse toggle is now visible.
		expect(
			screen.getByText(/\[t\]settings\.audioEnhancement\.customFiltersTitle/),
		).toBeInTheDocument();
	});
});

// ─────────────────────────────────────────────────────────────────────
// W3-A6 / XA-5 friction-items continuation.
//
// Verifies the additional XA-5 items implemented in Wave 3 Agent 6:
//   • XA-5-4 — useFilterState persists values across re-mounts via
//     sessionStorage.
//   • XA-5-8 — TestReviewPanel renders a recommendation block per
//     detected issue (text always; one-click CTA when onApplyPreset
//     is wired).
//   • XA-5-11 — CloudProvidersPanel renders an eye-icon show/hide
//     toggle next to the API key input.
//   • XA-5-13 — useMicrophoneTestSession exposes a module-level cache
//     reset helper (the cache itself is exercised indirectly via the
//     session hook's stop/start/selectMicrophone paths).
//   • XA-5-15 — VocabSearchFilterBar renders a count badge next to
//     the sort Select.
//   • XA-5-17 — Models page computes an ``activeModelSummary`` from
//     the lifecycle.config (verified by source scan — mounting the
//     full page requires too many mock dependencies for a focused
//     unit test).
//   • XA-5-20 — Import buttons carry a ``title`` attribute pointing
//     at the importFormatHint i18n key.
//   • XA-5-22 — microphone.use i18n value is the clearer
//     "Use this microphone" (locale-parity checked for all 8 files).
//
// Tests run on LINUX (sandbox).
// ─────────────────────────────────────────────────────────────────────

describe("XA-5-4 — useFilterState persists values across re-mounts", () => {
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

describe("XA-5-8 — TestReviewPanel renders per-issue recommendations", () => {
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

describe("XA-5-11 — CloudProvidersPanel renders the API-key eye toggle", () => {
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

describe("XA-5-13 — useMicrophoneTestSession exposes a cache-reset helper", () => {
	it("exports _resetMicrophoneTestCache as a function", async () => {
		const mod = await import(
			"@/pages/microphone/hooks/useMicrophoneTestSession"
		);
		expect(typeof mod._resetMicrophoneTestCache).toBe("function");
	});
});

describe("XA-5-15 — VocabSearchFilterBar renders a count badge", () => {
	afterEach(() => {
		cleanup();
	});

	it("renders the count badge with the entry count", () => {
		render(
			<VocabSearchFilterBar
				searchQuery=""
				onSearchChange={vi.fn()}
				sortOrder="newest"
				onSortOrderChange={vi.fn()}
				entryCount={42}
			/>,
		);
		const badge = screen.getByTestId("vocab-entry-count-badge");
		// The badge text comes from tChoice("vocabulary.count", 42)
		// — tChoice is NOT mocked (the test mock only overrides t()),
		// so the real catalogue lookup returns "42 corrections" in
		// the en locale. The point of the assertion is that the
		// entry count (42) is surfaced in the badge text.
		expect(badge.textContent).toMatch(/42/);
	});
});

describe("XA-5-17 — Models page computes an active-model summary", () => {
	it("source contains the activeModelSummary useMemo block", () => {
		const src = fs.readFileSync(
			path.join(RENDERER_SRC_ROOT, "pages", "Models.tsx"),
			"utf8",
		);
		// The active-model summary is computed via useMemo from the
		// lifecycle.config — verify the computation + the render
		// block exist. A regression that drops either would
		// re-break XA-5-17.
		expect(src).toMatch(/activeModelSummary\s*=\s*useMemo/);
		expect(src).toMatch(/data-testid="models-active-model-summary"/);
		expect(src).toMatch(/t\("models\.activeModelSummaryLabel"\)/);
	});
});

describe("XA-5-20 — Import buttons carry a format-hint title attribute", () => {
	it("VocabToolbar Import button title points at the importFormatHint key", () => {
		const src = fs.readFileSync(
			path.join(
				RENDERER_SRC_ROOT,
				"pages",
				"vocabulary",
				"components",
				"VocabToolbar.tsx",
			),
			"utf8",
		);
		expect(src).toMatch(/title=\{t\("vocabulary\.importFormatHint"\)\}/);
	});

	it("TemplateToolbar Import button title points at the importFormatHint key", () => {
		const src = fs.readFileSync(
			path.join(
				RENDERER_SRC_ROOT,
				"pages",
				"templates",
				"components",
				"TemplateToolbar.tsx",
			),
			"utf8",
		);
		expect(src).toMatch(/title=\{t\("templates\.importFormatHint"\)\}/);
	});
});

describe("XA-5-22 — microphone.use i18n value is the clearer label", () => {
	it.each(Object.keys(LOCALES))(
		"locale `%s` defines microphone.use with a non-empty value",
		(locale) => {
			const cat = LOCALES[locale] as unknown as {
				microphone?: { use?: string };
			};
			expect(typeof cat.microphone?.use).toBe("string");
			expect(cat.microphone?.use?.length ?? 0).toBeGreaterThan(0);
		},
	);

	it("en.json microphone.use is the explicit 'Use this microphone' label", () => {
		const enCat = en as unknown as {
			microphone?: { use?: string };
		};
		expect(enCat.microphone?.use).toBe("Use this microphone");
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
		"models.activeModelSummaryLabel",
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
