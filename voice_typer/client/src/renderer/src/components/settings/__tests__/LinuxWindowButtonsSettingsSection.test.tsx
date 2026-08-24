/**
 * Tests for LinuxWindowButtonsSettingsSection.
 *
 * Covers: Linux-only gating, the system-mode info row (following-system
 * vs unavailable), custom-mode controls (side select + three switches),
 * and that every edit commits the COMPLETE linux_window_buttons object
 * (the server validator requires all 5 keys).
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { SettingsSectionSharedProps } from "@/components/settings/types";
import { TooltipProvider } from "@/components/ui/tooltip";
import type {
	LinuxWindowButtonsConfig,
	VoiceTyperConfig,
} from "@/types/config";

const LINUX_UA =
	"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36";

// IS_LINUX is a module-load constant derived from the UA — stub a Linux
// UA and import the component fresh so the gate resolves true.
async function loadSection() {
	vi.spyOn(window.navigator, "userAgent", "get").mockReturnValue(LINUX_UA);
	vi.resetModules();
	const mod = await import(
		"@/components/settings/LinuxWindowButtonsSettingsSection"
	);
	return mod.LinuxWindowButtonsSettingsSection;
}

const renderWithProviders = (ui: React.ReactElement) =>
	render(<TooltipProvider delayDuration={200}>{ui}</TooltipProvider>);

function makeProps(
	linuxWindowButtons: Partial<LinuxWindowButtonsConfig> | undefined,
	linux_window_buttons_system?: VoiceTyperConfig["linux_window_buttons_system"],
): SettingsSectionSharedProps {
	return {
		config: {
			linux_window_buttons: linuxWindowButtons,
			linux_window_buttons_system: linux_window_buttons_system,
		} as unknown as VoiceTyperConfig,
		updateConfig: vi.fn(),
		updateConfigDebounced: vi.fn(),
		isVisible: vi.fn(() => true),
	};
}

const SYSTEM_SNAPSHOT = {
	desktop_environment: "gnome" as const,
	layout: { side: "left" as const, buttons: ["close", "minimize"] },
};

beforeEach(() => {
	vi.spyOn(window.navigator, "userAgent", "get").mockReturnValue(LINUX_UA);
});

afterEach(() => {
	cleanup();
	vi.restoreAllMocks();
	vi.resetModules();
});

describe("LinuxWindowButtonsSettingsSection", () => {
	it("renders the section with the system-mode info row (following system)", async () => {
		const Section = await loadSection();
		const props = makeProps({ mode: "system" }, SYSTEM_SNAPSHOT);
		renderWithProviders(<Section {...props} />);
		expect(screen.getByText("Window Buttons")).toBeTruthy();
		expect(screen.getByText("Following your desktop's layout")).toBeTruthy();
		// System mode: no visibility switches.
		expect(screen.queryByLabelText("Show close button")).toBeNull();
	});

	it("system snapshot missing → shows the unavailable note", async () => {
		const Section = await loadSection();
		const props = makeProps(
			{ mode: "system" },
			{ desktop_environment: "unknown", layout: null },
		);
		renderWithProviders(<Section {...props} />);
		expect(
			screen.getByText(
				"System button layout unavailable — using the default (right side, all buttons).",
			),
		).toBeTruthy();
	});

	it("custom mode renders side + visibility controls", async () => {
		const Section = await loadSection();
		const props = makeProps({
			mode: "custom",
			side: "left",
			show_minimize: true,
			show_maximize: false,
			show_close: true,
		});
		renderWithProviders(<Section {...props} />);
		expect(screen.getByLabelText("Show minimize button")).toBeTruthy();
		expect(screen.getByLabelText("Show maximize button")).toBeTruthy();
		expect(screen.getByLabelText("Show close button")).toBeTruthy();
	});

	it("toggling a switch commits the COMPLETE linux_window_buttons object", async () => {
		const Section = await loadSection();
		const props = makeProps({
			mode: "custom",
			side: "left",
			show_minimize: true,
			show_maximize: false,
			show_close: true,
		});
		renderWithProviders(<Section {...props} />);
		fireEvent.click(screen.getByLabelText("Show maximize button"));
		expect(props.updateConfig).toHaveBeenCalledWith({
			linux_window_buttons: {
				mode: "custom",
				side: "left",
				show_minimize: true,
				show_maximize: true,
				show_close: true,
			},
		});
	});
});
