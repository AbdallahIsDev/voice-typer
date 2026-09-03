/**
 * Sound-volume slider renders as PERCENTAGES (0-100, "%" suffix) while
 * the config value stays the canonical 0..1 float (C-CONF-1). The
 * display layer owns the conversion — a 0.65 config renders 65, a 65
 * slider commit persists 0.65.
 *
 * (Supersedes the 0..1 raw-decimal slider contract previously pinned by
 * RecordingSettingsSection.sounds.test.tsx — that file's two slider
 * assertions were updated to the percent contract.)
 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@hugeicons/react", () => ({
	HugeiconsIcon: () => <span data-testid="hugeicon" />,
}));

vi.mock("@hugeicons/core-free-icons", async () => {
	const { createHugeiconsMock } = await import(
		"@/__tests__/helpers/hugeicons-mock"
	);
	return createHugeiconsMock();
});

vi.mock("@/lib/sound-manager", () => ({
	playSoundCue: vi.fn(),
	setSoundFeedbackEnabled: vi.fn(),
	setSoundVolume: vi.fn(),
}));

vi.mock("@/components/hotkey/HotkeyPicker", () => ({
	HotkeyPicker: () => <div data-testid="hotkey-picker" />,
}));

vi.mock("@/components/feedback/InfoTooltip", () => ({
	InfoTooltip: ({ text }: { text: string }) => (
		<span data-testid="info-tooltip" data-text={text} />
	),
}));

// Capture RangeSlider instances so tests can drive onChange + read props.
const sliderInstances: Array<{
	ariaLabel: string;
	value: number;
	min: number;
	max: number;
	step: number;
	suffix: string;
	onChange: (v: number) => void;
	disabled: boolean;
}> = [];

vi.mock("@/components/common/RangeSlider", () => ({
	RangeSlider: (props: {
		ariaLabel?: string;
		value: number;
		min: number;
		max: number;
		step: number;
		suffix?: string;
		onChange: (v: number) => void;
		disabled?: boolean;
	}) => {
		sliderInstances.push({
			ariaLabel: props.ariaLabel ?? "",
			value: props.value,
			min: props.min,
			max: props.max,
			step: props.step,
			suffix: props.suffix ?? "",
			onChange: props.onChange,
			disabled: props.disabled ?? false,
		});
		return (
			<div
				data-testid="range-slider"
				data-aria-label={props.ariaLabel ?? ""}
				data-value={props.value}
				data-disabled={String(props.disabled ?? false)}
			/>
		);
	},
}));

import { makeConfig } from "@/__tests__/helpers/fixtures";
import { RecordingSettingsSection } from "@/components/settings/RecordingSettingsSection";
import { setSoundVolume } from "@/lib/sound-manager";

const alwaysVisible = () => true;
const noopUpdate = () => {};

function renderSection(configOverrides: Record<string, unknown> = {}) {
	return render(
		<RecordingSettingsSection
			config={makeConfig({ sound_feedback_enabled: true, ...configOverrides })}
			updateConfig={noopUpdate}
			updateConfigDebounced={noopUpdate}
			isVisible={alwaysVisible}
		/>,
	);
}

describe("RecordingSettingsSection — sound volume slider percent formatting", () => {
	beforeEach(() => {
		sliderInstances.length = 0;
		vi.clearAllMocks();
	});

	afterEach(() => {
		cleanup();
	});

	it("renders the slider in percent units with a % suffix", () => {
		renderSection({ sound_volume: 0.65 });
		const slider = screen.getByTestId("range-slider");
		expect(slider.getAttribute("data-value")).toBe("65");
		const inst = sliderInstances[0];
		expect(inst?.min).toBe(0);
		expect(inst?.max).toBe(100);
		expect(inst?.step).toBe(5);
		expect(inst?.suffix).toBe("%");
	});

	it("a 0.65 config value renders 65 (not the raw decimal)", () => {
		renderSection({ sound_volume: 0.65 });
		expect(screen.getByTestId("range-slider").getAttribute("data-value")).toBe(
			"65",
		);
	});

	it("a slider commit of 65 persists 0.65 and syncs the manager", () => {
		const updateConfigDebounced = vi.fn();
		render(
			<RecordingSettingsSection
				config={makeConfig({ sound_feedback_enabled: true })}
				updateConfig={noopUpdate}
				updateConfigDebounced={updateConfigDebounced}
				isVisible={alwaysVisible}
			/>,
		);
		const slider = sliderInstances[0];
		slider?.onChange(65);
		expect(updateConfigDebounced).toHaveBeenCalledWith("sound_volume", 0.65);
		expect(setSoundVolume).toHaveBeenCalledWith(0.65);
	});

	it("out-of-range slider commits clamp back into the 0..1 config domain", () => {
		const updateConfigDebounced = vi.fn();
		render(
			<RecordingSettingsSection
				config={makeConfig({ sound_feedback_enabled: true })}
				updateConfig={noopUpdate}
				updateConfigDebounced={updateConfigDebounced}
				isVisible={alwaysVisible}
			/>,
		);
		sliderInstances[0]?.onChange(250);
		expect(updateConfigDebounced).toHaveBeenCalledWith("sound_volume", 1);
	});
});
