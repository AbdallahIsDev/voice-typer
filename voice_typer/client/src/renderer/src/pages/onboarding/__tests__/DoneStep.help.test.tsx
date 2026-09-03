/**
 * test: the onboarding Done step renders a punctuation
 * cheat-sheet link + a `?`-shortcut tip, both feeding the SAME shared
 * HelpOverlay component (page-local instance, since the wizard page
 * can't reach App.tsx's overlay).
 */
import {
	cleanup,
	fireEvent,
	render,
	screen,
	waitFor,
} from "@testing-library/react";
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

import { DoneStep } from "@/pages/onboarding/components/DoneStep";

const baseProps = {
	headingRef: {
		current: null,
	} as unknown as React.RefObject<HTMLHeadingElement>,
	selectedHotkey: "<caps_lock>",
	selectedModel: "tiny",
	selectedMic: "",
	microphones: [],
	selectedBackend: "local" as const,
};

describe("DoneStep — cheat-sheet link + ? hint", () => {
	afterEach(() => {
		cleanup();
	});

	it("renders the cheat-sheet link with a stable testid + the ? hint chips", () => {
		render(<DoneStep {...baseProps} />);
		expect(screen.getByTestId("done-step-cheatsheet-link")).toBeTruthy();
		expect(screen.getByTestId("onboarding-done-help")).toBeTruthy();
		// The link uses the existing help.openCheatSheet label.
		expect(screen.getByTestId("done-step-cheatsheet-link").textContent).toBe(
			"Open punctuation cheat sheet",
		);
	});

	it("the `?` hint renders keycap chips, not a '+' text join (C-UI-1)", () => {
		render(<DoneStep {...baseProps} />);
		// HotkeyChips renders <kbd> chips for each key.
		const chips = screen.getAllByText("?").length;
		expect(chips).toBeGreaterThan(0);
		expect(
			screen.getByTestId("onboarding-done-help").textContent.includes("+"),
		).toBe(false);
	});

	it("clicking the link opens the shared HelpOverlay", async () => {
		render(<DoneStep {...baseProps} />);
		fireEvent.click(screen.getByTestId("done-step-cheatsheet-link"));
		// HelpOverlay's Modal mounts with role="dialog" when open.
		await waitFor(() => {
			expect(
				document.querySelector('[role="dialog"][data-state="open"]'),
			).toBeTruthy();
		});
	});
});
