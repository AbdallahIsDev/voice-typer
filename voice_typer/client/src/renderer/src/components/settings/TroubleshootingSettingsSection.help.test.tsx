/**
 * test: the Troubleshooting section renders a "Keyboard
 * Shortcuts" button that requests the parent-owned HelpOverlay
 * (onOpenHelp). The overlay instance itself lives in Settings.tsx —
 * the section only dispatches the request.
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@hugeicons/react", () => ({
	HugeiconsIcon: () => <span data-testid="hugeicon" />,
}));

vi.mock("@hugeicons/core-free-icons", async () => {
	const { createHugeiconsMock } = await import(
		"@/__tests__/helpers/hugeicons-mock"
	);
	return createHugeiconsMock();
});

vi.mock("@/hooks/usePython", () => ({
	usePython: () => ({ call: vi.fn() }),
}));

vi.mock("@/hooks/useSnackbar", () => ({
	useSnackbar: () => ({ showSnack: vi.fn() }),
}));

import { TroubleshootingSettingsSection } from "@/components/settings/TroubleshootingSettingsSection";

const alwaysVisible = () => true;

function renderSection(onOpenHelp: () => void) {
	return render(
		<TroubleshootingSettingsSection
			isVisible={alwaysVisible}
			updateConfig={() => {}}
			onResetClick={() => {}}
			onOpenHelp={onOpenHelp}
		/>,
	);
}

describe("TroubleshootingSettingsSection — Keyboard Shortcuts button", () => {
	afterEach(() => {
		cleanup();
	});

	it("renders the button with a stable testid and the existing help.title label", () => {
		renderSection(() => {});
		const btn = screen.getByTestId("keyboard-shortcuts-button");
		expect(btn).toBeTruthy();
		// help.title exists in every locale ("Keyboard Shortcuts").
		expect(btn.textContent).toContain("Keyboard Shortcuts");
	});

	it("calls onOpenHelp when clicked", () => {
		const onOpenHelp = vi.fn();
		renderSection(onOpenHelp);
		fireEvent.click(screen.getByTestId("keyboard-shortcuts-button"));
		expect(onOpenHelp).toHaveBeenCalledTimes(1);
	});

	it("the button stays visible when the search query matches its label", () => {
		render(
			<TroubleshootingSettingsSection
				// Only the "Keyboard Shortcuts" label matches the query.
				isVisible={(label) => label.includes("Keyboard Shortcuts")}
				updateConfig={() => {}}
				onResetClick={() => {}}
				onOpenHelp={() => {}}
			/>,
		);
		expect(screen.getByTestId("keyboard-shortcuts-button")).toBeTruthy();
	});
});
