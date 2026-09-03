/**
 * MicToggleButton error state — when the last recording attempt failed
 * (`error` prop), the idle button renders a distinct hollow-destructive
 * treatment with an alert glyph and `aria-live="polite"`, instead of
 * the solid glow of the healthy idle button.
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
	hugeiconsCoreMock,
	hugeiconsReactMock,
} from "@/__tests__/helpers/stableMocks";
import { MicToggleButton } from "@/pages/home/components/MicToggleButton";

vi.mock("@hugeicons/react", () => hugeiconsReactMock());
vi.mock("@hugeicons/core-free-icons", () => hugeiconsCoreMock());

afterEach(() => {
	cleanup();
});

function renderButton(overrides: Record<string, unknown> = {}) {
	return render(
		<MicToggleButton
			isRecording={false}
			toggling={false}
			disabled={false}
			onClick={() => {}}
			label="Start dictation"
			{...overrides}
		/>,
	);
}

describe("MicToggleButton error state", () => {
	it("idle without error: alert glyph absent, no aria-live, no testid error marker", () => {
		renderButton();
		const btn = screen.getByRole("button", { name: "Start dictation" });
		expect(btn.getAttribute("aria-live")).toBeNull();
		expect(
			screen.queryByTestId("hugeicon")?.getAttribute("data-name"),
		).not.toBe("AlertCircleIcon");
	});

	it("error: alert glyph renders and the button carries aria-live=polite", () => {
		renderButton({ error: true });
		const btn = screen.getByRole("button", { name: "Start dictation" });
		expect(btn.getAttribute("aria-live")).toBe("polite");
		expect(screen.getByTestId("hugeicon").getAttribute("data-name")).toBe(
			"AlertCircleIcon",
		);
	});

	it("error while recording: recording state takes precedence (stop glyph, no aria-live)", () => {
		renderButton({ error: true, isRecording: true, label: "Stop dictation" });
		const btn = screen.getByRole("button", { name: "Stop dictation" });
		expect(btn.getAttribute("aria-live")).toBeNull();
		expect(screen.getByTestId("hugeicon").getAttribute("data-name")).toBe(
			"StopIcon",
		);
	});

	it("click still invokes onClick in the error state", () => {
		const onClick = vi.fn();
		renderButton({ error: true, onClick });
		fireEvent.click(screen.getByRole("button", { name: "Start dictation" }));
		expect(onClick).toHaveBeenCalledTimes(1);
	});
});
