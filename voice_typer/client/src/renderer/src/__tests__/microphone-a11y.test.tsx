/**
 *  /  /  /  — accessibility + perf fixes for the
 * Microphone page components.
 *
 * This file mounts the real components and asserts on the rendered DOM
 * (not on source substrings) so a refactor that preserves the contract
 * still passes and a behavioural regression fails.
 *
 * Mock strategy:
 *   - `@hugeicons/react` and `@hugeicons/core-free-icons` are stubbed
 *     so we don't pull in the real icon runtime.
 *   - `@/components/audio/AudioFilterChain` is stubbed so the
 *     AudioPresetSelector test doesn't mount the full settings row
 *     graph (irrelevant to 's memoization invariant).
 *   - `@/i18n/i18n` is mocked with a `t` spy that returns the real
 *     English value (loaded from en.json) AND records every call so
 *      can assert that `getPresetOptions()` runs exactly once per
 *     mount instead of three times.
 */
import { cleanup, render, screen } from "@testing-library/react";
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

vi.mock("@hugeicons/core-free-icons", () => {
	const make = (name: string) => ({ name });
	return {
		ArrowDown01Icon: make("ArrowDown01Icon"),
		ArrowUp01Icon: make("ArrowUp01Icon"),
		FilterIcon: make("FilterIcon"),
		Mic02Icon: make("Mic02Icon"),
		MicOff01Icon: make("MicOff01Icon"),
		PlayIcon: make("PlayIcon"),
		RefreshIcon: make("RefreshIcon"),
		StopIcon: make("StopIcon"),
		Tick02Icon: make("Tick02Icon"),
		UnfoldMoreIcon: make("UnfoldMoreIcon"),
	};
});

vi.mock("@/components/audio/AudioFilterChain", () => ({
	AudioFilterChain: () => <div data-testid="audio-filter-chain" />,
}));

import { AudioPresetSelector } from "@/components/microphone/AudioPresetSelector";
import { MicrophoneListItem } from "@/components/microphone/MicrophoneListItem";
import { TestReviewPanel } from "@/components/microphone/TestReviewPanel";
import { AvailableMicrophonesList } from "@/pages/microphone/components/AvailableMicrophonesList";
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

// Minimal VoiceTyperConfig — only the fields AudioPresetSelector itself
// reads (audio_preset). The full object is too noisy for this unit test.
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
//MicrophoneListItem 'Use' button aria-label includes mic name
// ──────────────────────────────────────────────────────────────────────
describe("BG-45: MicrophoneListItem 'Use' button has a per-mic aria-label", () => {
	it("uses the mic name in the aria-label so SR users can distinguish buttons", () => {
		render(
			<MicrophoneListItem
				mic={micA}
				isSystemDefault={false}
				onSelect={() => {}}
			/>,
		);

		const useButton = document.querySelector("button");
		expect(useButton).toBeTruthy();
		// Visible text is still just "Use" (microphone.use).
		expect(useButton?.textContent).toBe("Use");
		// Accessible name includes the mic name (useMicAria key +
		// {name} interpolation).
		expect(useButton?.getAttribute("aria-label")).toBe(
			"Use microphone — Blue Yeti X",
		);
	});

	it("produces different aria-labels for different microphones", () => {
		const { rerender } = render(
			<MicrophoneListItem
				mic={micA}
				isSystemDefault={false}
				onSelect={() => {}}
			/>,
		);
		const labelA = document.querySelector("button")?.getAttribute("aria-label");
		expect(labelA).toBe("Use microphone — Blue Yeti X");

		rerender(
			<MicrophoneListItem
				mic={micB}
				isSystemDefault={false}
				onSelect={() => {}}
			/>,
		);
		const labelB = document.querySelector("button")?.getAttribute("aria-label");
		expect(labelB).toBe("Use microphone — Rode NT-USB");

		// Two different mics → two different aria-labels. This is the
		// exact regression the finding called out (5 mics → 5 identical
		// "Use" buttons).
		expect(labelA).not.toBe(labelB);
	});
});

// ──────────────────────────────────────────────────────────────────────
//TestReviewPanel exposes quality updates via aria-live +
// role=status, and hides the decorative bullet from AT.
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

	it("wraps the quality block in an aria-live=polite region with aria-atomic", () => {
		render(
			<TestReviewPanel
				durationMs={5000}
				quality={qualityWithIssues}
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

		// The quality summary (containing the "Estimated Transcription
		// Quality" label) is wrapped in an aria-live region.
		const qualityLabel = document.querySelector("span.text-xs.font-medium");
		// Find the live region ancestor of the quality label.
		const liveRegion = qualityLabel?.closest('[aria-live="polite"]');
		expect(liveRegion).toBeTruthy();
		expect(liveRegion?.getAttribute("aria-atomic")).toBe("true");
	});

	it("marks the detected-issues heading with role=status", () => {
		render(
			<TestReviewPanel
				durationMs={5000}
				quality={qualityWithIssues}
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

		// The "Detected Issues:" heading carries role="status" so SR
		// users are alerted when issues appear after a test.
		const detectedIssuesHeading = Array.from(
			document.querySelectorAll("span"),
		).find((el) => el.textContent === "Detected Issues:");
		expect(detectedIssuesHeading).toBeTruthy();
		expect(detectedIssuesHeading?.getAttribute("role")).toBe("status");
	});

	it("hides the decorative bullet glyph from assistive tech", () => {
		render(
			<TestReviewPanel
				durationMs={5000}
				quality={qualityWithIssues}
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

		// The "•" bullet is purely decorative — its meaning is conveyed
		// by list structure, so it must be aria-hidden.
		const bulletSpans = Array.from(
			document.querySelectorAll("span.text-amber-500"),
		).filter((el) => el.textContent === "•");
		expect(bulletSpans.length).toBeGreaterThanOrEqual(1);
		for (const span of bulletSpans) {
			expect(span.getAttribute("aria-hidden")).toBe("true");
		}
	});
});

// ──────────────────────────────────────────────────────────────────────
//AvailableMicrophonesList uses real list semantics (ul/li).
// ──────────────────────────────────────────────────────────────────────
describe("BG-72: AvailableMicrophonesList renders a real list with ul/li + roles", () => {
	it("wraps the mic rows in a <ul role=list>", () => {
		render(
			<AvailableMicrophonesList
				microphones={[micA, micB]}
				otherMicrophones={[micA, micB]}
				isSystemDefault={false}
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

	it("renders one <li role=listitem> per row (system default + each mic)", () => {
		render(
			<AvailableMicrophonesList
				microphones={[micA, micB]}
				otherMicrophones={[micA, micB]}
				isSystemDefault={false}
				testRunning={false}
				onSelectMicrophone={() => {}}
			/>,
		);

		// 1 system-default row + 2 mic rows = 3 list items. Native <li>
		// elements expose the "listitem" role implicitly.
		const items = screen.getAllByRole("listitem");
		expect(items.length).toBe(3);
	});

	it("renders the no-other-mics notice as a list item (not a bare div)", () => {
		render(
			<AvailableMicrophonesList
				microphones={[micA]}
				otherMicrophones={[]}
				isSystemDefault={false}
				testRunning={false}
				onSelectMicrophone={() => {}}
			/>,
		);

		// 1 system-default row + 1 "no other mics" notice row = 2 items.
		const items = screen.getAllByRole("listitem");
		expect(items.length).toBe(2);

		// The "No other microphones available" text lives inside a real
		// <li> (which exposes the listitem role), not a bare <div>.
		const notice = items.find((li) =>
			li.textContent?.includes("No other microphones available"),
		);
		expect(notice).toBeTruthy();
		expect(notice?.tagName.toLowerCase()).toBe("li");
	});

	it("does not render any bare <div> as a direct child of the list (all rows are <li>)", () => {
		render(
			<AvailableMicrophonesList
				microphones={[micA, micB]}
				otherMicrophones={[micA, micB]}
				isSystemDefault={false}
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
//AudioPresetSelector memoizes getPresetOptions() instead of
// calling it 3× per render.
// ──────────────────────────────────────────────────────────────────────
describe("BG-94: AudioPresetSelector memoizes getPresetOptions() to a single call per mount", () => {
	it("calls t('settings.audioEnhancement.presetAuto') exactly once on initial render", () => {
		// Before the fix, getPresetOptions() was invoked inline at
		// three sites (header label, <Select> options, description
		// lookup). Each call internally calls t() for every preset's
		// label + description (10 keys total, including
		// presetAuto). With 3 inline call sites, presetAuto would be
		// looked up 3 times per render. After the fix, the array is
		// memoized via useMemo(() => getPresetOptions(), []) so each
		// preset key is looked up exactly once.
		render(
			<AudioPresetSelector
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
			<AudioPresetSelector
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
			<AudioPresetSelector
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

	it("still renders the correct header label + Select options + description for the current preset", () => {
		// Behavioral smoke test: even with memoization, the visible
		// DOM must still show the right preset's label and
		// description.
		render(
			<AudioPresetSelector
				preset="studio"
				config={minimalConfig}
				showAdvanced={true}
				onPresetChange={() => {}}
				onToggleAdvanced={() => {}}
				onConfigChange={() => {}}
			/>,
		);

		// Header label (chevron span) should contain "Studio".
		// Use text-based lookup since the Tailwind class
		// `text-(--text-muted)` contains parens that break CSS
		// selectors. We expect the header to render the current
		// preset's label, which for "studio" is the EN value
		// "Studio".
		const allSpans = Array.from(document.querySelectorAll("span"));
		const headerText = allSpans.map((s) => s.textContent ?? "").join(" ");
		expect(headerText).toContain("Studio");

		// The Select trigger renders the current preset's label.
		const selectTrigger =
			document.querySelector("[data-slot='select-trigger']") ??
			document.querySelector("button[role='combobox']");
		expect(selectTrigger).toBeTruthy();
		expect(selectTrigger?.textContent).toContain("Studio");
	});
});
