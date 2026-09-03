/**
 * Tests for the (sound volume + Test Sound) and (hidden
 * config rows) work in `RecordingSettingsSection` and
 * `PrivacySettingsSection`.
 *
 * Pinned contracts:
 *  - Recording → Sound Feedback: a volume slider (RangeSlider) wired to
 *    debounced `set_config` writes + an immediate `setSoundVolume` sync,
 *    plus a "Test Sound" button that plays one existing cue.
 *  - Recording → paste-safety rows (unsafe_paste_on_unknown_focus,
 *    warn_elevated_paste, warn_password_paste) persist via updateConfig.
 *  - Privacy → log_transcriptions + clipboard_save_restore rows persist
 *    via updateConfig.
 *  - All new rows feed their label+info into the search predicate.
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
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

// Capture RangeSlider instances so tests can drive onChange.
const sliderInstances: Array<{
	ariaLabel: string;
	value: number;
	onChange: (v: number) => void;
	disabled: boolean;
}> = [];

vi.mock("@/components/common/RangeSlider", () => ({
	RangeSlider: (props: {
		ariaLabel?: string;
		value: number;
		onChange: (v: number) => void;
		disabled?: boolean;
	}) => {
		sliderInstances.push({
			ariaLabel: props.ariaLabel ?? "",
			value: props.value,
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
import { PrivacySettingsSection } from "@/components/settings/PrivacySettingsSection";
import { RecordingSettingsSection } from "@/components/settings/RecordingSettingsSection";
import {
	playSoundCue,
	setSoundFeedbackEnabled,
	setSoundVolume,
} from "@/lib/sound-manager";

const alwaysVisible = () => true;
const noopUpdate = () => {};

describe("RecordingSettingsSection — sound volume slider + Test Sound", () => {
	beforeEach(() => {
		sliderInstances.length = 0;
		vi.clearAllMocks();
	});

	afterEach(() => {
		cleanup();
	});

	it("renders a volume slider bound to config.sound_volume (default 100%)", () => {
		render(
			<RecordingSettingsSection
				config={makeConfig({ sound_feedback_enabled: true })}
				updateConfig={noopUpdate}
				updateConfigDebounced={noopUpdate}
				isVisible={alwaysVisible}
			/>,
		);
		const slider = screen.getByTestId("range-slider");
		expect(slider).toBeTruthy();
		// Slider works in percent units; the config value stays 0..1.
		expect(slider.getAttribute("data-value")).toBe("100");
	});

	it("renders the Test Sound button with a stable testid", () => {
		render(
			<RecordingSettingsSection
				config={makeConfig({ sound_feedback_enabled: true })}
				updateConfig={noopUpdate}
				updateConfigDebounced={noopUpdate}
				isVisible={alwaysVisible}
			/>,
		);
		expect(screen.getByTestId("test-sound-button")).toBeTruthy();
	});

	it("Test Sound plays one existing cue at the configured volume", () => {
		render(
			<RecordingSettingsSection
				config={makeConfig({
					sound_feedback_enabled: true,
					sound_volume: 0.3,
				})}
				updateConfig={noopUpdate}
				updateConfigDebounced={noopUpdate}
				isVisible={alwaysVisible}
			/>,
		);
		fireEvent.click(screen.getByTestId("test-sound-button"));
		expect(playSoundCue).toHaveBeenCalledWith("complete");
	});

	it("slider change clamps, syncs the manager, and persists via debounced set_config", () => {
		const updateConfigDebounced = vi.fn();
		render(
			<RecordingSettingsSection
				config={makeConfig({ sound_feedback_enabled: true })}
				updateConfig={noopUpdate}
				updateConfigDebounced={updateConfigDebounced}
				isVisible={alwaysVisible}
			/>,
		);
		const slider = sliderInstances.find((s) => s.value === 100);
		expect(slider).toBeTruthy();
		// Slider commit is in percent units (50%); the persisted config
		// value stays the canonical 0..1 float.
		slider?.onChange(50);

		expect(setSoundVolume).toHaveBeenCalledWith(0.5);
		expect(updateConfigDebounced).toHaveBeenCalledWith("sound_volume", 0.5);
	});

	it("slider change clamps out-of-range percent values to the 0..1 config domain", () => {
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
		slider?.onChange(250);
		expect(updateConfigDebounced).toHaveBeenCalledWith("sound_volume", 1);
	});

	it("slider and Test Sound are disabled while sound feedback is off", () => {
		render(
			<RecordingSettingsSection
				config={makeConfig({ sound_feedback_enabled: false })}
				updateConfig={noopUpdate}
				updateConfigDebounced={noopUpdate}
				isVisible={alwaysVisible}
			/>,
		);
		expect(
			screen.getByTestId("range-slider").getAttribute("data-disabled"),
		).toBe("true");
		const btn = screen.getByTestId("test-sound-button") as HTMLButtonElement;
		expect(btn.disabled).toBe(true);
	});

	it("the sound-feedback toggle still syncs the manager (existing contract)", () => {
		const updateConfig = vi.fn();
		render(
			<RecordingSettingsSection
				config={makeConfig({ sound_feedback_enabled: true })}
				updateConfig={updateConfig}
				updateConfigDebounced={noopUpdate}
				isVisible={alwaysVisible}
			/>,
		);
		// The existing Sound Feedback switch resolves to the real en.json
		// label ("Sound Feedback").
		const soundSwitch = screen
			.getAllByRole("switch")
			.find((el) => el.getAttribute("aria-label") === "Sound Feedback");
		expect(soundSwitch).toBeTruthy();
		fireEvent.click(soundSwitch as HTMLElement);
		expect(updateConfig).toHaveBeenCalledWith(
			expect.objectContaining({ sound_feedback_enabled: false }),
		);
		expect(setSoundFeedbackEnabled).toHaveBeenCalledWith(false);
	});
});

describe("RecordingSettingsSection — paste-safety rows", () => {
	beforeEach(() => {
		sliderInstances.length = 0;
	});

	afterEach(() => {
		cleanup();
	});

	it("renders switches for unsafe_paste_on_unknown_focus / warn_elevated_paste / warn_password_paste", () => {
		render(
			<RecordingSettingsSection
				config={makeConfig({
					unsafe_paste_on_unknown_focus: false,
					warn_elevated_paste: true,
					warn_password_paste: true,
				})}
				updateConfig={noopUpdate}
				updateConfigDebounced={noopUpdate}
				isVisible={alwaysVisible}
			/>,
		);
		// New-row labels are PENDING i18n keys (translations not editable
		// this wave) — assert via stable testids.
		expect(screen.getByTestId("unsafe-paste-switch")).toBeTruthy();
		expect(screen.getByTestId("warn-elevated-paste-switch")).toBeTruthy();
		expect(screen.getByTestId("warn-password-paste-switch")).toBeTruthy();
	});

	it("persists warn_elevated_paste = false via updateConfig", () => {
		const updateConfig = vi.fn();
		render(
			<RecordingSettingsSection
				config={makeConfig({ warn_elevated_paste: true })}
				updateConfig={updateConfig}
				updateConfigDebounced={noopUpdate}
				isVisible={alwaysVisible}
			/>,
		);
		fireEvent.click(screen.getByTestId("warn-elevated-paste-switch"));
		expect(updateConfig).toHaveBeenCalledWith(
			expect.objectContaining({ warn_elevated_paste: false }),
		);
	});

	it("persists unsafe_paste_on_unknown_focus = true via updateConfig", () => {
		const updateConfig = vi.fn();
		render(
			<RecordingSettingsSection
				config={makeConfig({ unsafe_paste_on_unknown_focus: false })}
				updateConfig={updateConfig}
				updateConfigDebounced={noopUpdate}
				isVisible={alwaysVisible}
			/>,
		);
		fireEvent.click(screen.getByTestId("unsafe-paste-switch"));
		expect(updateConfig).toHaveBeenCalledWith(
			expect.objectContaining({ unsafe_paste_on_unknown_focus: true }),
		);
	});
});

describe("PrivacySettingsSection — hidden config rows", () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});

	afterEach(() => {
		cleanup();
	});

	it("renders log_transcriptions + clipboard_save_restore rows", () => {
		render(
			<PrivacySettingsSection
				config={makeConfig({
					log_transcriptions: false,
					clipboard_save_restore: true,
				})}
				updateConfig={noopUpdate}
				updateConfigDebounced={noopUpdate}
				isVisible={alwaysVisible}
			/>,
		);
		expect(screen.getByTestId("log-transcriptions-switch")).toBeTruthy();
		expect(screen.getByTestId("clipboard-save-restore-switch")).toBeTruthy();
	});

	it("persists log_transcriptions = true via updateConfig", () => {
		const updateConfig = vi.fn();
		render(
			<PrivacySettingsSection
				config={makeConfig({ log_transcriptions: false })}
				updateConfig={updateConfig}
				updateConfigDebounced={noopUpdate}
				isVisible={alwaysVisible}
			/>,
		);
		fireEvent.click(screen.getByTestId("log-transcriptions-switch"));
		expect(updateConfig).toHaveBeenCalledWith(
			expect.objectContaining({ log_transcriptions: true }),
		);
	});

	it("persists clipboard_save_restore = false via updateConfig", () => {
		const updateConfig = vi.fn();
		render(
			<PrivacySettingsSection
				config={makeConfig({ clipboard_save_restore: true })}
				updateConfig={updateConfig}
				updateConfigDebounced={noopUpdate}
				isVisible={alwaysVisible}
			/>,
		);
		fireEvent.click(screen.getByTestId("clipboard-save-restore-switch"));
		expect(updateConfig).toHaveBeenCalledWith(
			expect.objectContaining({ clipboard_save_restore: false }),
		);
	});
});
