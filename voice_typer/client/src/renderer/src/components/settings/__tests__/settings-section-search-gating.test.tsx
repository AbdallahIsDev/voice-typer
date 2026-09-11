/**
 * Per-row search filtering for the Settings sections that previously
 * rendered their ENTIRE card when a query matched any single row.
 *
 * Contract (mirrors the gated exemplars, General/Audio): a search
 * query that matches one row must hide the OTHER rows of the same
 * section while the section header stays; clearing the query restores
 * every row. The predicate is the page-level `isVisible` prop
 * (pages/Settings.tsx `_filter_settings`); these tests drive it
 * directly with label-matching fakes the same way the existing
 * per-row filtering tests do.
 *
 * Sections covered: Recording, Overlay, LLM Polishing, AI Enhancement
 * (+ Vocabulary Automation), Diagnostics, Resources & Feedback,
 * Troubleshooting.
 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { makeConfig } from "@/__tests__/helpers/fixtures";

// Shared stable-mocks preamble (see helpers/stableMocks.tsx): the
// assertable singletons + one vi.mock line per module.
import {
	hugeiconsCoreMock,
	hugeiconsReactMock,
	nextThemesMock,
	pythonMock,
	resetStableMocks,
	snackbarMock,
	sonnerMock,
} from "@/__tests__/helpers/stableMocks";
import { TooltipProvider } from "@/components/ui/tooltip";

vi.mock("@hugeicons/react", () => hugeiconsReactMock());
vi.mock("@hugeicons/core-free-icons", () => hugeiconsCoreMock());
vi.mock("@/hooks/usePython", () => pythonMock());
vi.mock("@/hooks/useSnackbar", () => snackbarMock());
vi.mock("sonner", () => sonnerMock());
vi.mock("next-themes", () => nextThemesMock());

// HotkeyPicker's capture internals (keyboard listeners, platform
// presets) are irrelevant to row filtering, render a stub and assert
// on the row labels instead.
vi.mock("@/components/hotkey/HotkeyPicker", () => ({
	HotkeyPicker: () => <div data-testid="hotkey-picker" />,
}));

// Keep the recording-section's sound cues silent and localStorage-free.
vi.mock("@/lib/sound-manager", () => ({
	setSoundFeedbackEnabled: vi.fn(),
	setSoundVolume: vi.fn(),
	playSoundCue: vi.fn(),
}));

import { AiEnhancementSettingsSection } from "@/components/settings/AiEnhancementSettingsSection";
import { DiagnosticsSettingsSection } from "@/components/settings/DiagnosticsSettingsSection";
import { LlmPolishingSettingsSection } from "@/components/settings/LlmPolishingSettingsSection";
import { OverlaySettingsSection } from "@/components/settings/OverlaySettingsSection";
import { RecordingSettingsSection } from "@/components/settings/RecordingSettingsSection";
import { ResourcesSettingsSection } from "@/components/settings/ResourcesSettingsSection";
import {
	anyRowVisible,
	GatedSettingRow,
} from "@/components/settings/settingsRowGating";
import { TroubleshootingSettingsSection } from "@/components/settings/TroubleshootingSettingsSection";
import type { SettingsSectionSharedProps } from "@/components/settings/types";

const renderWithProviders = (ui: React.ReactElement) =>
	render(<TooltipProvider delayDuration={200}>{ui}</TooltipProvider>);

const alwaysVisible: SettingsSectionSharedProps["isVisible"] = () => true;

/** isVisible predicate that hides rows whose label doesn't include `q`. */
function filterByLabel(q: string): SettingsSectionSharedProps["isVisible"] {
	const query = q.toLowerCase();
	return (label) => label.toLowerCase().includes(query);
}

const noopUpdate = () => {};
const noopDebounced = () => {};

afterEach(() => {
	cleanup();
	resetStableMocks();
});

// ─────────────────────────────────────────────────────────────────────
// Recording (the largest section, 14 rows)
// ─────────────────────────────────────────────────────────────────────
describe("RecordingSettingsSection, in-section search filtering", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		cleanup();
	});

	it("hides non-matching rows when a query matches one row", () => {
		renderWithProviders(
			<RecordingSettingsSection
				config={makeConfig()}
				updateConfig={noopUpdate}
				updateConfigDebounced={noopDebounced}
				isVisible={filterByLabel("dictation")}
			/>,
		);

		// The matching row stays.
		expect(screen.getByText("Dictation Key")).toBeTruthy();
		// Every other row in the section is hidden.
		expect(screen.queryByText("Re-Paste Key")).toBeNull();
		expect(screen.queryByText("Recording Mode")).toBeNull();
		expect(screen.queryByText("Stop on Silence")).toBeNull();
		expect(screen.queryByText("ESC to Cancel")).toBeNull();
		expect(screen.queryByText("Auto-Paste")).toBeNull();
		expect(screen.queryByText("Paste into unidentified windows")).toBeNull();
		expect(screen.queryByText("Confirm paste into admin windows")).toBeNull();
		expect(screen.queryByText("Confirm paste into password fields")).toBeNull();
		expect(screen.queryByText("Sound Feedback")).toBeNull();
		expect(screen.queryByText("Sound volume")).toBeNull();
		expect(screen.queryByText("Test Sound")).toBeNull();
		expect(screen.queryByText("Silence Warning")).toBeNull();
		expect(screen.queryByText("Max Recording Time")).toBeNull();
	});

	it("restores all rows when the query is cleared", () => {
		const utils = renderWithProviders(
			<RecordingSettingsSection
				config={makeConfig()}
				updateConfig={noopUpdate}
				updateConfigDebounced={noopDebounced}
				isVisible={filterByLabel("dictation")}
			/>,
		);
		expect(screen.queryByText("Re-Paste Key")).toBeNull();

		utils.rerender(
			<TooltipProvider delayDuration={200}>
				<RecordingSettingsSection
					config={makeConfig()}
					updateConfig={noopUpdate}
					updateConfigDebounced={noopDebounced}
					isVisible={alwaysVisible}
				/>
			</TooltipProvider>,
		);

		expect(screen.getByText("Dictation Key")).toBeTruthy();
		expect(screen.getByText("Re-Paste Key")).toBeTruthy();
		expect(screen.getByText("Recording Mode")).toBeTruthy();
		expect(screen.getByText("Stop on Silence")).toBeTruthy();
		expect(screen.getByText("ESC to Cancel")).toBeTruthy();
		expect(screen.getByText("Auto-Paste")).toBeTruthy();
		expect(screen.getByText("Sound Feedback")).toBeTruthy();
		expect(screen.getByText("Silence Warning")).toBeTruthy();
		expect(screen.getByText("Max Recording Time")).toBeTruthy();
	});

	it("still renders the section header while filtering", () => {
		renderWithProviders(
			<RecordingSettingsSection
				config={makeConfig()}
				updateConfig={noopUpdate}
				updateConfigDebounced={noopDebounced}
				isVisible={filterByLabel("dictation")}
			/>,
		);
		expect(screen.getByText("Hotkeys & Recording")).toBeTruthy();
	});
});

// ─────────────────────────────────────────────────────────────────────
// Overlay
// ─────────────────────────────────────────────────────────────────────
describe("OverlaySettingsSection, in-section search filtering", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		cleanup();
	});

	it("hides non-matching rows when a query matches one row", () => {
		renderWithProviders(
			<OverlaySettingsSection
				config={makeConfig({ bubble_behavior: "always_visible" })}
				updateConfig={noopUpdate}
				updateConfigDebounced={noopDebounced}
				isVisible={filterByLabel("drag")}
			/>,
		);

		expect(screen.getByText("Drag to Move")).toBeTruthy();
		expect(screen.queryByText("Bubble Behavior")).toBeNull();
		expect(screen.queryByText("Bubble Position")).toBeNull();
		expect(screen.queryByText("Show on App Startup")).toBeNull();
		expect(screen.queryByText("Bubble Mic Button")).toBeNull();
	});

	it("restores all rows when the query is cleared", () => {
		const utils = renderWithProviders(
			<OverlaySettingsSection
				config={makeConfig({ bubble_behavior: "always_visible" })}
				updateConfig={noopUpdate}
				updateConfigDebounced={noopDebounced}
				isVisible={filterByLabel("drag")}
			/>,
		);
		expect(screen.queryByText("Bubble Behavior")).toBeNull();

		utils.rerender(
			<TooltipProvider delayDuration={200}>
				<OverlaySettingsSection
					config={makeConfig({ bubble_behavior: "always_visible" })}
					updateConfig={noopUpdate}
					updateConfigDebounced={noopDebounced}
					isVisible={alwaysVisible}
				/>
			</TooltipProvider>,
		);

		expect(screen.getByText("Bubble Behavior")).toBeTruthy();
		expect(screen.getByText("Bubble Position")).toBeTruthy();
		expect(screen.getByText("Show on App Startup")).toBeTruthy();
		expect(screen.getByText("Drag to Move")).toBeTruthy();
		expect(screen.getByText("Bubble Mic Button")).toBeTruthy();
	});

	it("still renders the section header while filtering", () => {
		renderWithProviders(
			<OverlaySettingsSection
				config={makeConfig({ bubble_behavior: "always_visible" })}
				updateConfig={noopUpdate}
				updateConfigDebounced={noopDebounced}
				isVisible={filterByLabel("drag")}
			/>,
		);
		expect(screen.getByText("Overlay")).toBeTruthy();
	});
});

// ─────────────────────────────────────────────────────────────────────
// LLM Polishing
// ─────────────────────────────────────────────────────────────────────
describe("LlmPolishingSettingsSection, in-section search filtering", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		cleanup();
	});

	it("hides non-matching rows when a query matches one row", () => {
		renderWithProviders(
			<LlmPolishingSettingsSection
				config={makeConfig({ llm_polish: true })}
				updateConfig={noopUpdate}
				updateConfigDebounced={noopDebounced}
				isVisible={filterByLabel("model")}
			/>,
		);

		expect(screen.getByText("Model")).toBeTruthy();
		expect(screen.queryByText("Enable")).toBeNull();
		expect(screen.queryByText("API Key")).toBeNull();
		expect(screen.queryByText("API URL")).toBeNull();
		expect(screen.queryByText("Preset")).toBeNull();
	});

	it("restores all rows when the query is cleared", () => {
		const utils = renderWithProviders(
			<LlmPolishingSettingsSection
				config={makeConfig({ llm_polish: true })}
				updateConfig={noopUpdate}
				updateConfigDebounced={noopDebounced}
				isVisible={filterByLabel("model")}
			/>,
		);
		expect(screen.queryByText("API Key")).toBeNull();

		utils.rerender(
			<TooltipProvider delayDuration={200}>
				<LlmPolishingSettingsSection
					config={makeConfig({ llm_polish: true })}
					updateConfig={noopUpdate}
					updateConfigDebounced={noopDebounced}
					isVisible={alwaysVisible}
				/>
			</TooltipProvider>,
		);

		expect(screen.getByText("Enable")).toBeTruthy();
		expect(screen.getByText("API Key")).toBeTruthy();
		expect(screen.getByText("API URL")).toBeTruthy();
		expect(screen.getByText("Model")).toBeTruthy();
		expect(screen.getByText("Preset")).toBeTruthy();
	});

	it("still renders the section header while filtering", () => {
		renderWithProviders(
			<LlmPolishingSettingsSection
				config={makeConfig({ llm_polish: true })}
				updateConfig={noopUpdate}
				updateConfigDebounced={noopDebounced}
				isVisible={filterByLabel("model")}
			/>,
		);
		expect(screen.getByText("LLM Polishing")).toBeTruthy();
	});
});

// ─────────────────────────────────────────────────────────────────────
// AI Enhancement + Vocabulary Automation
// ─────────────────────────────────────────────────────────────────────
describe("AiEnhancementSettingsSection, in-section search filtering", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		cleanup();
	});

	it("hides non-matching rows when a query matches one row", () => {
		renderWithProviders(
			<AiEnhancementSettingsSection
				config={makeConfig()}
				updateConfig={noopUpdate}
				updateConfigDebounced={noopDebounced}
				isVisible={filterByLabel("grammar")}
			/>,
		);

		// Matching row stays; the other AI Enhancement rows hide.
		expect(screen.getByText("Fix Grammar Basics")).toBeTruthy();
		expect(screen.queryByText("Enable AI Enhancement")).toBeNull();
		expect(screen.queryByText("Auto-Punctuate")).toBeNull();
		expect(screen.queryByText("Auto-Capitalize")).toBeNull();
		// No Vocabulary Automation row matches either, the whole
		// section is hidden (section-level check still applies).
		expect(screen.queryByText("Enable Vocabulary Automation")).toBeNull();
	});

	it("restores all rows in both sections when the query is cleared", () => {
		const utils = renderWithProviders(
			<AiEnhancementSettingsSection
				config={makeConfig()}
				updateConfig={noopUpdate}
				updateConfigDebounced={noopDebounced}
				isVisible={filterByLabel("grammar")}
			/>,
		);
		expect(screen.queryByText("Enable AI Enhancement")).toBeNull();

		utils.rerender(
			<TooltipProvider delayDuration={200}>
				<AiEnhancementSettingsSection
					config={makeConfig()}
					updateConfig={noopUpdate}
					updateConfigDebounced={noopDebounced}
					isVisible={alwaysVisible}
				/>
			</TooltipProvider>,
		);

		expect(screen.getByText("Enable AI Enhancement")).toBeTruthy();
		expect(screen.getByText("Fix Grammar Basics")).toBeTruthy();
		expect(screen.getByText("Auto-Punctuate")).toBeTruthy();
		expect(screen.getByText("Auto-Capitalize")).toBeTruthy();
		expect(screen.getByText("Enable Vocabulary Automation")).toBeTruthy();
		expect(screen.getByText("Suggest-Below Confidence")).toBeTruthy();
		expect(screen.getByText("Auto-Apply Confidence")).toBeTruthy();
	});

	it("still renders the matching section header while filtering", () => {
		renderWithProviders(
			<AiEnhancementSettingsSection
				config={makeConfig()}
				updateConfig={noopUpdate}
				updateConfigDebounced={noopDebounced}
				isVisible={filterByLabel("grammar")}
			/>,
		);
		expect(screen.getByText("AI Enhancement")).toBeTruthy();
		// The non-matching sibling section's header is gone.
		expect(screen.queryByText("Vocabulary Automation")).toBeNull();
	});
});

// ─────────────────────────────────────────────────────────────────────
// Diagnostics
// ─────────────────────────────────────────────────────────────────────
describe("DiagnosticsSettingsSection, in-section search filtering", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		cleanup();
	});

	it("hides non-matching rows when a query matches one row", () => {
		renderWithProviders(
			<DiagnosticsSettingsSection isVisible={filterByLabel("microphone")} />,
		);

		expect(screen.getByText("Microphone")).toBeTruthy();
		expect(screen.queryByText("App Version")).toBeNull();
		expect(screen.queryByText("Backend")).toBeNull();
		expect(screen.queryByText("Config Directory")).toBeNull();
		expect(screen.queryByText("Speech recognizer")).toBeNull();
		expect(screen.queryByText("Device")).toBeNull();
		expect(screen.queryByText("Hotkey")).toBeNull();
	});

	it("restores all rows when the query is cleared", () => {
		const utils = renderWithProviders(
			<DiagnosticsSettingsSection isVisible={filterByLabel("microphone")} />,
		);
		expect(screen.queryByText("App Version")).toBeNull();

		utils.rerender(
			<TooltipProvider delayDuration={200}>
				<DiagnosticsSettingsSection isVisible={alwaysVisible} />
			</TooltipProvider>,
		);

		expect(screen.getByText("App Version")).toBeTruthy();
		expect(screen.getByText("Backend")).toBeTruthy();
		expect(screen.getByText("Config Directory")).toBeTruthy();
		expect(screen.getByText("Speech recognizer")).toBeTruthy();
		expect(screen.getByText("Device")).toBeTruthy();
		expect(screen.getByText("Hotkey")).toBeTruthy();
		expect(screen.getByText("Microphone")).toBeTruthy();
	});

	it("still renders the section header while filtering", () => {
		renderWithProviders(
			<DiagnosticsSettingsSection isVisible={filterByLabel("microphone")} />,
		);
		expect(screen.getByText("Diagnostics")).toBeTruthy();
	});
});

// ─────────────────────────────────────────────────────────────────────
// Resources & Feedback
// ─────────────────────────────────────────────────────────────────────
describe("ResourcesSettingsSection, in-section search filtering", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		cleanup();
	});

	it("hides non-matching links when a query matches one link", () => {
		renderWithProviders(
			<ResourcesSettingsSection isVisible={filterByLabel("changelog")} />,
		);

		expect(screen.getByText("View Changelog")).toBeTruthy();
		expect(screen.queryByText("Documentation")).toBeNull();
		expect(screen.queryByText("GitHub Repository")).toBeNull();
		expect(screen.queryByText("Report a Bug")).toBeNull();
		expect(screen.queryByText("Request a Feature")).toBeNull();
		expect(screen.queryByText("Security Policy")).toBeNull();
		expect(screen.queryByText("Contributing")).toBeNull();
	});

	it("restores all links when the query is cleared", () => {
		const utils = renderWithProviders(
			<ResourcesSettingsSection isVisible={filterByLabel("changelog")} />,
		);
		expect(screen.queryByText("Documentation")).toBeNull();

		utils.rerender(
			<TooltipProvider delayDuration={200}>
				<ResourcesSettingsSection isVisible={alwaysVisible} />
			</TooltipProvider>,
		);

		expect(screen.getByText("Documentation")).toBeTruthy();
		expect(screen.getByText("View Changelog")).toBeTruthy();
		expect(screen.getByText("GitHub Repository")).toBeTruthy();
		expect(screen.getByText("Report a Bug")).toBeTruthy();
		expect(screen.getByText("Request a Feature")).toBeTruthy();
		expect(screen.getByText("Security Policy")).toBeTruthy();
		expect(screen.getByText("Contributing")).toBeTruthy();
	});

	it("still renders the section header while filtering", () => {
		renderWithProviders(
			<ResourcesSettingsSection isVisible={filterByLabel("changelog")} />,
		);
		expect(screen.getByText("Resources & Feedback")).toBeTruthy();
	});
});

// ─────────────────────────────────────────────────────────────────────
// Troubleshooting
// ─────────────────────────────────────────────────────────────────────
describe("TroubleshootingSettingsSection, in-section search filtering", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		cleanup();
	});

	it("hides non-matching rows when a query matches one row", () => {
		renderWithProviders(
			<TroubleshootingSettingsSection
				isVisible={filterByLabel("wizard")}
				updateConfig={noopUpdate}
				onResetClick={noopUpdate}
				onOpenHelp={noopUpdate}
			/>,
		);

		expect(screen.getByText("Re-run Setup Wizard")).toBeTruthy();
		expect(screen.queryByText("Open Log Folder")).toBeNull();
		expect(screen.queryByText("Help & FAQ")).toBeNull();
		expect(screen.queryByText("Report a Bug")).toBeNull();
		expect(screen.queryByText("Keyboard Shortcuts")).toBeNull();
		expect(screen.queryByText("Reset to Defaults")).toBeNull();
	});

	it("restores all rows when the query is cleared", () => {
		const utils = renderWithProviders(
			<TroubleshootingSettingsSection
				isVisible={filterByLabel("wizard")}
				updateConfig={noopUpdate}
				onResetClick={noopUpdate}
				onOpenHelp={noopUpdate}
			/>,
		);
		expect(screen.queryByText("Open Log Folder")).toBeNull();

		utils.rerender(
			<TooltipProvider delayDuration={200}>
				<TroubleshootingSettingsSection
					isVisible={alwaysVisible}
					updateConfig={noopUpdate}
					onResetClick={noopUpdate}
					onOpenHelp={noopUpdate}
				/>
			</TooltipProvider>,
		);

		expect(screen.getByText("Open Log Folder")).toBeTruthy();
		expect(screen.getByText("Help & FAQ")).toBeTruthy();
		expect(screen.getByText("Report a Bug")).toBeTruthy();
		expect(screen.getByText("Keyboard Shortcuts")).toBeTruthy();
		expect(screen.getByText("Re-run Setup Wizard")).toBeTruthy();
		expect(screen.getByText("Reset to Defaults")).toBeTruthy();
	});

	it("still renders the section header while filtering", () => {
		renderWithProviders(
			<TroubleshootingSettingsSection
				isVisible={filterByLabel("wizard")}
				updateConfig={noopUpdate}
				onResetClick={noopUpdate}
				onOpenHelp={noopUpdate}
			/>,
		);
		expect(screen.getByText("Troubleshooting")).toBeTruthy();
	});
});

// ─────────────────────────────────────────────────────────────────────
// Shared helper primitives (settingsRowGating)
// ─────────────────────────────────────────────────────────────────────
describe("settingsRowGating helpers", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		cleanup();
	});

	it("anyRowVisible returns true when any row matches the predicate", () => {
		const isVisible: SettingsSectionSharedProps["isVisible"] = (
			label,
			info,
			sectionTitle,
		) => label === "Row B" || info === "needle" || sectionTitle === "Heading";
		const rows = [
			{ label: "Row A", info: "nothing" },
			{ label: "Row B" },
			{ label: "Row C" },
		];
		expect(anyRowVisible(isVisible, "Heading", rows)).toBe(true);
	});

	it("anyRowVisible returns false when no row matches", () => {
		const isVisible: SettingsSectionSharedProps["isVisible"] = () => false;
		expect(anyRowVisible(isVisible, "Heading", [{ label: "Row A" }])).toBe(
			false,
		);
	});

	it("GatedSettingRow renders the row (label + control) when the predicate accepts it", () => {
		renderWithProviders(
			<GatedSettingRow
				isVisible={alwaysVisible}
				sectionTitle="Heading"
				label="Accepted Row"
				info="tooltip text"
			>
				<button type="button">control</button>
			</GatedSettingRow>,
		);
		expect(screen.getByText("Accepted Row")).toBeTruthy();
		expect(screen.getByText("control")).toBeTruthy();
	});

	it("GatedSettingRow renders nothing when the predicate rejects the row", () => {
		const { container } = renderWithProviders(
			<GatedSettingRow
				isVisible={filterByLabel("zzz-no-match")}
				sectionTitle="Heading"
				label="Rejected Row"
			>
				<button type="button">control</button>
			</GatedSettingRow>,
		);
		expect(screen.queryByText("Rejected Row")).toBeNull();
		expect(screen.queryByText("control")).toBeNull();
		expect(container.firstChild).toBeNull();
	});

	it("GatedSettingRow matches against searchInfo (not the rendered tooltip) when both are provided", () => {
		// The predicate only matches the searchable description, the row
		// must still render (with the DIFFERENT tooltip string).
		const matchesSearchInfoOnly: SettingsSectionSharedProps["isVisible"] = (
			label,
			info,
		) => info === "searchable keywords" && label === "Split Row";
		renderWithProviders(
			<GatedSettingRow
				isVisible={matchesSearchInfoOnly}
				sectionTitle="Heading"
				label="Split Row"
				info="rendered tooltip"
				searchInfo="searchable keywords"
			>
				<button type="button">control</button>
			</GatedSettingRow>,
		);
		expect(screen.getByText("Split Row")).toBeTruthy();
		expect(screen.getByText("control")).toBeTruthy();
	});

	it("GatedSettingRow hides the row when only the tooltip (not searchInfo) would match", () => {
		// The predicate matches the TOOLTIP text but the searchable
		// description differs, the row hides (searchInfo wins).
		const matchesTooltipOnly: SettingsSectionSharedProps["isVisible"] = (
			_label,
			info,
		) => info === "rendered tooltip";
		renderWithProviders(
			<GatedSettingRow
				isVisible={matchesTooltipOnly}
				sectionTitle="Heading"
				label="Split Row"
				info="rendered tooltip"
				searchInfo="searchable keywords"
			>
				<button type="button">control</button>
			</GatedSettingRow>,
		);
		expect(screen.queryByText("Split Row")).toBeNull();
	});
});
