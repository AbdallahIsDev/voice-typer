/**
 * Accessibility + perf pins for the Microphone page components.
 *
 * This file mounts the real components and asserts on the rendered DOM
 * (not on source substrings) so a refactor that preserves the contract
 * still passes and a behavioural regression fails.
 *
 * Mock strategy:
 *   - `@hugeicons/react` and `@hugeicons/core-free-icons` are stubbed
 *     so we don't pull in the real icon runtime.
 *   - `@/components/audio/AudioFilterChain` is stubbed so the
 *     PresetAccordionSelector test doesn't mount the full settings row
 *     graph (irrelevant to its memoization invariant).
 *   - `@/i18n/i18n` is mocked with a `t` spy that returns the real
 *     English value (loaded from en.json) AND records every call so we
 *     can assert that `getPresetOptions()` runs exactly once per mount
 *     instead of per render.
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import enMessages from "@/i18n/translations/en.json";

// Flatten the nested en.json into dot-separated keys, mirroring what
// the real i18n module does at load time. Used by the t() spy below.
const enFlat = new Map<string, string>();
function flatten(obj: Record<string, unknown>, prefix = ""): void {
	for (const [k, v] of Object.entries(obj)) {
		const key = prefix ? `${prefix}.${k}` : k;
		if (v && typeof v === "object") {
			flatten(v as Record<string, unknown>, key);
		} else if (typeof v === "string") {
			enFlat.set(key, v);
		}
	}
}
flatten(enMessages as Record<string, unknown>);

// Hoist the spy so the vi.mock factory can reference it.
const { tSpy } = vi.hoisted(() => ({
	tSpy: vi.fn((key: string, params?: Record<string, string>) => {
		let value = enFlat.get(key) ?? key;
		if (params) {
			for (const [k, v] of Object.entries(params)) {
				value = value.replace(new RegExp(`\\{${k}\\}`, "g"), v);
			}
		}
		return value;
	}),
}));

vi.mock("@/i18n/i18n", () => ({ t: tSpy }));

vi.mock("@hugeicons/react", () => ({
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
}));

vi.mock("@hugeicons/core-free-icons", async () => {
	const { createHugeiconsMock } = await import(
		"@/__tests__/helpers/hugeicons-mock"
	);
	return createHugeiconsMock();
});

vi.mock("@/components/audio/AudioFilterChain", () => ({
	AudioFilterChain: () => <div data-testid="audio-filter-chain" />,
}));

import { MicrophoneListItem } from "@/components/microphone/MicrophoneListItem";
import { TestReviewPanel } from "@/components/microphone/TestReviewPanel";
import { RadioGroup } from "@/components/ui/radio-group";
import { AvailableMicrophonesList } from "@/pages/microphone/components/AvailableMicrophonesList";
import { PresetAccordionSelector } from "@/pages/microphone/components/PresetAccordionSelector";
import type { MicrophoneDevice, VoiceTyperConfig } from "@/types/config";

// ── Fixtures ──────────────────────────────────────────────────────────
const micA: MicrophoneDevice = {
	index: 0,
	id: "mic-a",
	name: "Blue Yeti X",
	host_api: "ALSA",
	default: false,
	channels: 2,
	rate: 48000,
};

const micB: MicrophoneDevice = {
	index: 1,
	id: "mic-b",
	name: "Rode NT-USB",
	host_api: "ALSA",
	default: false,
	channels: 1,
	rate: 44100,
};

// Minimal VoiceTyperConfig — only the fields PresetAccordionSelector
// itself reads (audio_preset + the noise_filter_* fields handed to the
// stubbed AudioFilterChain). The full object is too noisy for this unit
// test.
const minimalConfig = {
	audio_preset: "auto",
} as unknown as VoiceTyperConfig;

// ── Test setup ────────────────────────────────────────────────────────
beforeEach(() => {
	tSpy.mockClear();
	cleanup();
});

afterEach(() => {
	cleanup();
});

// ──────────────────────────────────────────────────────────────────────
//Mic list radio rows: each row exposes an accessible name, checked
// state tracks the selection, and items are REALLY disabled during a test.
// ──────────────────────────────────────────────────────────────────────
describe("BG-45: mic selection rows expose radio semantics with per-row accessible names", () => {
	it("renders one radiogroup whose radios carry the device names (System Default included)", () => {
		render(
			<AvailableMicrophonesList
				microphones={[micA, micB]}
				activeMicId={null}
				testRunning={false}
				onSelectMicrophone={() => {}}
			/>,
		);

		const group = screen.getByRole("radiogroup");
		expect(group).toBeTruthy();

		// Per-row accessible names — the regression BG-45 called out was
		// N indistinguishable controls; each radio now announces WHICH
		// microphone it selects.
		expect(screen.getByRole("radio", { name: "Blue Yeti X" })).toBeTruthy();
		expect(screen.getByRole("radio", { name: "Rode NT-USB" })).toBeTruthy();
		expect(screen.getByRole("radio", { name: "System Default" })).toBeTruthy();
	});

	it("marks the active selection via aria-checked on its radio", () => {
		render(
			<AvailableMicrophonesList
				microphones={[micA, micB]}
				activeMicId="mic-a"
				testRunning={false}
				onSelectMicrophone={() => {}}
			/>,
		);

		expect(screen.getByRole("radio", { name: "Blue Yeti X" })).toBeChecked();
		expect(
			screen.getByRole("radio", { name: "Rode NT-USB" }),
		).not.toBeChecked();
	});

	it("disables every radio while a test recording is running", () => {
		render(
			<AvailableMicrophonesList
				microphones={[micA, micB]}
				activeMicId="mic-a"
				testRunning={true}
				onSelectMicrophone={() => {}}
			/>,
		);

		for (const name of ["System Default", "Blue Yeti X", "Rode NT-USB"]) {
			expect(screen.getByRole("radio", { name })).toBeDisabled();
		}
	});
});

// ──────────────────────────────────────────────────────────────────────
//TestReviewPanel exposes quality updates via aria-live +
// role=status, hides the decorative bullet from AT, and uses the
// theme warning token (no hardcoded palette classes).
// ──────────────────────────────────────────────────────────────────────
describe("BG-71: TestReviewPanel quality block is announced to AT", () => {
	const qualityWithIssues = {
		volume_level: "good" as const,
		volume_rms: 0.1,
		peak_level: 0.5,
		noise_level: "moderate" as const,
		has_voice: true,
		has_clipping: false,
		detected_issues: ["Moderate background noise"],
		estimated_transcription_quality: 70,
		silence_ratio: 0.2,
	};

	function renderReviewPanel() {
		return render(
			<TestReviewPanel
				durationMs={5000}
				quality={qualityWithIssues}
				transcription={null}
				transcriptionUnavailable={false}
				testAudioBase64="data:audio/wav;base64,AAAA"
				rawAudioBase64={null}
				playing={false}
				playingOriginal={false}
				onPlayEnhanced={() => {}}
				onPlayOriginal={() => {}}
				onStop={() => {}}
				onRetest={() => {}}
				hasFiltersEnabled={false}
			/>,
		);
	}

	it("wraps the quality block in an aria-live=polite region with aria-atomic", () => {
		renderReviewPanel();

		// The quality summary (containing the "Estimated Transcription
		// Quality" label) is wrapped in an aria-live region.
		const qualityLabel = document.querySelector("span.text-xs.font-medium");
		// Find the live region ancestor of the quality label.
		const liveRegion = qualityLabel?.closest('[aria-live="polite"]');
		expect(liveRegion).toBeTruthy();
		expect(liveRegion?.getAttribute("aria-atomic")).toBe("true");
	});

	it("marks the detected-issues heading with role=status", () => {
		renderReviewPanel();

		// The "Detected Issues:" heading carries role="status" so SR
		// users are alerted when issues appear after a test. It renders as
		// an <output> element — the semantic status live-region element,
		// whose implicit ARIA role is status. Assert the COMPUTED role via
		// toHaveRole (the literal role attribute is absent on <output>).
		const detectedIssuesHeading = Array.from(
			document.querySelectorAll("output"),
		).find((el) => el.textContent === "Detected Issues:");
		expect(detectedIssuesHeading).toBeTruthy();
		expect(detectedIssuesHeading).toHaveRole("status");
	});

	it("hides the decorative bullet glyph from assistive tech", () => {
		renderReviewPanel();

		// The "•" bullet is purely decorative — its meaning is conveyed
		// by list structure, so it must be aria-hidden. It uses the theme
		// warning TOKEN (text-warning), never a hardcoded palette class
		// like text-amber-500.
		const bulletSpans = Array.from(
			document.querySelectorAll("span.text-warning"),
		).filter((el) => el.textContent === "•");
		expect(bulletSpans.length).toBeGreaterThanOrEqual(1);
		for (const span of bulletSpans) {
			expect(span.getAttribute("aria-hidden")).toBe("true");
			expect(span.className).not.toContain("amber");
		}
	});
});

// ──────────────────────────────────────────────────────────────────────
//TestReviewPanel transcription display ("You said" / unavailable copy).
// ──────────────────────────────────────────────────────────────────────
describe("TestReviewPanel test-transcription display", () => {
	it("renders the transcription under the You-said label when present", () => {
		render(
			<TestReviewPanel
				durationMs={5000}
				quality={null}
				transcription="hello world"
				transcriptionUnavailable={false}
				testAudioBase64="data:audio/wav;base64,AAAA"
				rawAudioBase64={null}
				playing={false}
				playingOriginal={false}
				onPlayEnhanced={() => {}}
				onPlayOriginal={() => {}}
				onStop={() => {}}
				onRetest={() => {}}
				hasFiltersEnabled={false}
			/>,
		);

		expect(screen.getByText("You said")).toBeTruthy();
		expect(screen.getByTestId("test-transcription").textContent).toBe(
			"hello world",
		);
	});

	it("renders the localized explanation when no model can transcribe", () => {
		render(
			<TestReviewPanel
				durationMs={5000}
				quality={null}
				transcription={null}
				transcriptionUnavailable={true}
				testAudioBase64="data:audio/wav;base64,AAAA"
				rawAudioBase64={null}
				playing={false}
				playingOriginal={false}
				onPlayEnhanced={() => {}}
				onPlayOriginal={() => {}}
				onStop={() => {}}
				onRetest={() => {}}
				hasFiltersEnabled={false}
			/>,
		);

		expect(screen.getByText("You said")).toBeTruthy();
		expect(
			screen.getByTestId("test-transcription-unavailable").textContent,
		).toBe(enFlat.get("microphone.transcriptionUnavailable"));
	});

	it("renders neither line without transcription data", () => {
		render(
			<TestReviewPanel
				durationMs={5000}
				quality={null}
				transcription={null}
				transcriptionUnavailable={false}
				testAudioBase64="data:audio/wav;base64,AAAA"
				rawAudioBase64={null}
				playing={false}
				playingOriginal={false}
				onPlayEnhanced={() => {}}
				onPlayOriginal={() => {}}
				onStop={() => {}}
				onRetest={() => {}}
				hasFiltersEnabled={false}
			/>,
		);

		expect(screen.queryByText("You said")).toBeNull();
		expect(screen.queryByTestId("test-transcription")).toBeNull();
	});
});

// ──────────────────────────────────────────────────────────────────────
//AvailableMicrophonesList keeps real list semantics (ul/li) around the
// unified radio rows.
// ──────────────────────────────────────────────────────────────────────
describe("BG-72: AvailableMicrophonesList renders a real list with ul/li + roles", () => {
	it("wraps the mic rows in a <ul role=list>", () => {
		render(
			<AvailableMicrophonesList
				microphones={[micA, micB]}
				activeMicId={null}
				testRunning={false}
				onSelectMicrophone={() => {}}
			/>,
		);

		// Computed-role assertion: a native <ul> exposes the "list"
		// role in the accessibility tree even without an explicit
		// role="list" attribute (the ARIA-in-HTML mapping). Querying by
		// role verifies the actual AT exposure, not the markup detail.
		const ul = screen.getByRole("list");
		expect(ul).toBeTruthy();
		expect(ul.tagName.toLowerCase()).toBe("ul");
	});

	it("renders one <li role=listitem> per row (system default + every device)", () => {
		render(
			<AvailableMicrophonesList
				microphones={[micA, micB]}
				activeMicId="mic-a"
				testRunning={false}
				onSelectMicrophone={() => {}}
			/>,
		);

		// 1 system-default row + 2 device rows = 3 list items. The ACTIVE
		// device is present too (radio groups need the selected option in
		// the set). Native <li> elements expose the "listitem" role
		// implicitly.
		const items = screen.getAllByRole("listitem");
		expect(items.length).toBe(3);
	});

	it("does not render any bare <div> as a direct child of the list (all rows are <li>)", () => {
		render(
			<AvailableMicrophonesList
				microphones={[micA, micB]}
				activeMicId={null}
				testRunning={false}
				onSelectMicrophone={() => {}}
			/>,
		);

		const ul = screen.getByRole("list");
		expect(ul).toBeTruthy();
		const directDivChildren = Array.from(ul?.children ?? []).filter(
			(child) => child.tagName.toLowerCase() === "div",
		);
		expect(directDivChildren.length).toBe(0);
	});
});

// ──────────────────────────────────────────────────────────────────────
//PresetAccordionSelector memoizes getPresetOptions() instead of
// calling it inline per render.
// ──────────────────────────────────────────────────────────────────────
describe("BG-94: PresetAccordionSelector memoizes getPresetOptions() to a single call per mount", () => {
	it("calls t('settings.audioEnhancement.presetAuto') exactly once on initial render", () => {
		// The option array is built inside useMemo(() =>
		// getPresetOptions(), []) so each preset label/description key is
		// looked up exactly once per mount, no matter how often the
		// component re-renders (level pushes re-render the parent card).
		render(
			<PresetAccordionSelector
				preset="auto"
				config={minimalConfig}
				showAdvanced={true}
				onPresetChange={() => {}}
				onToggleAdvanced={() => {}}
				onConfigChange={() => {}}
			/>,
		);

		const presetAutoCalls = tSpy.mock.calls.filter(
			([key]) => key === "settings.audioEnhancement.presetAuto",
		).length;
		expect(presetAutoCalls).toBe(1);

		// Same invariant for the description key (proves both label
		// and description paths go through the memoized array).
		const presetAutoDescCalls = tSpy.mock.calls.filter(
			([key]) => key === "settings.audioEnhancement.presetAutoDescription",
		).length;
		expect(presetAutoDescCalls).toBe(1);
	});

	it("does NOT re-call getPresetOptions() on re-render when props change", () => {
		const { rerender } = render(
			<PresetAccordionSelector
				preset="auto"
				config={minimalConfig}
				showAdvanced={true}
				onPresetChange={() => {}}
				onToggleAdvanced={() => {}}
				onConfigChange={() => {}}
			/>,
		);
		const callsAfterFirstRender = tSpy.mock.calls.filter(
			([key]) => key === "settings.audioEnhancement.presetAuto",
		).length;
		expect(callsAfterFirstRender).toBe(1);

		// Re-render with a different preset. The memoized preset
		// array is reused (useMemo dep is []), so getPresetOptions()
		// is NOT called again — presetAuto should still have been
		// called only once total.
		rerender(
			<PresetAccordionSelector
				preset="studio"
				config={minimalConfig}
				showAdvanced={true}
				onPresetChange={() => {}}
				onToggleAdvanced={() => {}}
				onConfigChange={() => {}}
			/>,
		);

		const callsAfterRerender = tSpy.mock.calls.filter(
			([key]) => key === "settings.audioEnhancement.presetAuto",
		).length;
		expect(callsAfterRerender).toBe(1);
	});

	it("shows the current selection in the collapsed header and applies a radio pick immediately", () => {
		const handlePresetChange = vi.fn();
		const { rerender } = render(
			<PresetAccordionSelector
				preset="studio"
				config={minimalConfig}
				showAdvanced={false}
				onPresetChange={handlePresetChange}
				onToggleAdvanced={() => {}}
				onConfigChange={() => {}}
			/>,
		);

		// Collapsed header shows the section label + CURRENT selection.
		expect(screen.getByText("Microphone Quality")).toBeTruthy();
		expect(screen.getByTestId("mic-preset-current").textContent).toBe(
			"Studio (clean environment)",
		);

		// Expand, then pick another preset via its radio.
		fireEvent.click(screen.getByRole("button", { expanded: false }));
		fireEvent.click(screen.getByRole("radio", { name: "Off (raw audio)" }));
		expect(handlePresetChange).toHaveBeenCalledWith("off");

		// After the prop flips, the collapsed label follows.
		rerender(
			<PresetAccordionSelector
				preset="off"
				config={minimalConfig}
				showAdvanced={false}
				onPresetChange={handlePresetChange}
				onToggleAdvanced={() => {}}
				onConfigChange={() => {}}
			/>,
		);
		expect(screen.getByTestId("mic-preset-current").textContent).toBe(
			"Off (raw audio)",
		);
	});

	it("reveals the Custom-filters disclosure only under the custom preset", () => {
		let showAdvanced = false;
		const handleToggleAdvanced = () => {
			showAdvanced = !showAdvanced;
		};
		const { rerender } = render(
			<PresetAccordionSelector
				preset="auto"
				config={minimalConfig}
				showAdvanced={showAdvanced}
				onPresetChange={() => {}}
				onToggleAdvanced={handleToggleAdvanced}
				onConfigChange={() => {}}
			/>,
		);

		fireEvent.click(screen.getByRole("button", { expanded: false }));

		// Not custom → no Custom-filters toggle, no filter chain.
		expect(screen.queryByText("Custom filters")).toBeNull();

		rerender(
			<PresetAccordionSelector
				preset="custom"
				config={minimalConfig}
				showAdvanced={showAdvanced}
				onPresetChange={() => {}}
				onToggleAdvanced={handleToggleAdvanced}
				onConfigChange={() => {}}
			/>,
		);

		expect(screen.getByText("Custom filters")).toBeTruthy();
		expect(
			document.querySelector('[data-testid="audio-filter-chain"]'),
		).toBeFalsy();

		// Progressive disclosure: the toggle reveals the filter chain.
		fireEvent.click(screen.getByText("Custom filters"));
		rerender(
			<PresetAccordionSelector
				preset="custom"
				config={minimalConfig}
				showAdvanced={showAdvanced}
				onPresetChange={() => {}}
				onToggleAdvanced={handleToggleAdvanced}
				onConfigChange={() => {}}
			/>,
		);
		expect(
			document.querySelector('[data-testid="audio-filter-chain"]'),
		).toBeTruthy();
	});
});

// ──────────────────────────────────────────────────────────────────────
//MicrophoneListItem: badge + row rendering for the unified radio list.
// ──────────────────────────────────────────────────────────────────────
describe("MicrophoneListItem radio row", () => {
	function renderRowInGroup(ui: React.ReactElement) {
		// Radix RadioGroupItem requires a Root ancestor (production always
		// renders rows inside the list's single RadioGroup).
		return render(<RadioGroup>{ui}</RadioGroup>);
	}

	it("renders the OS-default badge with the accent foreground token when not the active selection", () => {
		renderRowInGroup(
			<MicrophoneListItem
				mic={{ ...micA, default: true }}
				checked={false}
				showDefaultBadge={true}
				disabled={false}
				onSelect={() => {}}
			/>,
		);

		const badge = screen.getByText("Default");
		expect(badge.className).toContain("bg-accent");
		expect(badge.className).toContain("text-accent-foreground");
		expect(badge.className).not.toContain("text-white");
	});

	it("omits the badge when the OS default IS the active selection", () => {
		renderRowInGroup(
			<MicrophoneListItem
				mic={{ ...micA, default: true }}
				checked={true}
				showDefaultBadge={false}
				disabled={false}
				onSelect={() => {}}
			/>,
		);

		expect(screen.queryByText("Default")).toBeNull();
	});

	it("exposes the mic name as the radio's accessible name", () => {
		renderRowInGroup(
			<MicrophoneListItem
				mic={micA}
				checked={false}
				showDefaultBadge={false}
				disabled={false}
				onSelect={() => {}}
			/>,
		);

		expect(screen.getByRole("radio", { name: "Blue Yeti X" })).toBeTruthy();
	});
});
