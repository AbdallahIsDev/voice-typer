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
 * Sections covered: Recording, AI Enhancement (+ Vocabulary Automation).
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
import { RecordingSettingsSection } from "@/components/settings/RecordingSettingsSection";
import {
	anyRowVisible,
	GatedSettingRow,
} from "@/components/settings/settingsRowGating";
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
// AI Enhancement + Vocabulary Automation
// ─────────────────────────────────────────────────────────────────────
describe("AiEnhancementSettingsSection, in-section search filtering", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		cleanup();
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
