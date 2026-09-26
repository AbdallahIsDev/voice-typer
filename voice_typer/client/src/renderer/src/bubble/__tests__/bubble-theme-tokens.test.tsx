import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Bubble } from "@/Bubble";

// The shared button className constant is the most important guard, it
// is imported by `BubbleMicButton`, `BubbleStopButton`, and
// `BubbleDismissButton`, so a single regression here propagates to all
// three affordances.
import { BUBBLE_BUTTON_CLASS, type BubbleMode } from "../constants";

// Stub window.bubble so <Bubble /> mounts without crashing. The renderer
// casts `window.bubble` to `BubbleWindowBubble | undefined` and calls
// `?.onShow` / `?.onHide` / `?.onSetState` / `?.onConfig`, we only need
// the listeners registered so the component mounts; we don't drive them.
function makeMockBubble() {
	const unsub = () => {};
	return {
		onLevel: () => unsub,
		onShow: () => unsub,
		onHide: () => unsub,
		onDraggable: () => unsub,
		onSetState: () => unsub,
		onConfig: () => unsub,
		signalReady: () => {},
		hideComplete: () => {},
		resizeTo: () => {},
		moveBy: () => {},
		toggleDictation: () => {},
		dismiss: () => {},
	};
}

describe("bubble theme-token parity (no raw zinc/white palette)", () => {
	it("BUBBLE_BUTTON_CLASS contains no `zinc-` substring", () => {
		expect(BUBBLE_BUTTON_CLASS).not.toMatch(/zinc-/);
	});

	it("BUBBLE_BUTTON_CLASS contains no `bg-white` / `dark:bg-zinc` substring", () => {
		expect(BUBBLE_BUTTON_CLASS).not.toMatch(/bg-white/);
		expect(BUBBLE_BUTTON_CLASS).not.toMatch(/dark:bg-zinc/);
	});

	it("BUBBLE_BUTTON_CLASS uses semantic tokens", () => {
		// Spot-check the three semantic tokens the migration introduced:
		// `--muted-foreground` (resting foreground), `--surface-hover` (hover
		// background), `--foreground` (hover foreground).
		expect(BUBBLE_BUTTON_CLASS).toContain("text-muted-foreground");
		expect(BUBBLE_BUTTON_CLASS).toContain("hover:bg-(--surface-hover)");
		expect(BUBBLE_BUTTON_CLASS).toContain("hover:text-foreground");
	});

	it("Bubble pill container uses semantic tokens (no raw palette)", () => {
		(window as unknown as Record<string, unknown>).bubble = makeMockBubble();
		try {
			render(<Bubble />);
			// The pill is the inner `<div>` with `rounded-full`, query it
			// by its border/bg utility classes (which are now semantic).
			// The border carries the muted /7 opacity modifier, so the
			// slash must be escaped in the CSS selector.
			const pill = document.querySelector(".bg-surface.border-border\\/5");
			expect(pill).toBeTruthy();
			// Negative assertions: no raw zinc/white palette on the pill.
			expect(pill?.className).not.toMatch(/bg-white/);
			expect(pill?.className).not.toMatch(/dark:bg-zinc/);
			expect(pill?.className).not.toMatch(/border-zinc-/);
			expect(pill?.className).not.toMatch(/dark:border-white/);
		} finally {
			delete (window as unknown as Record<string, unknown>).bubble;
		}
	});

	it("BubbleMode union includes the original + new mid-flow modes", () => {
		// Compile-time assertion: the union must accept all of these.
		const modes: BubbleMode[] = [
			"recording",
			"transcribing",
			"idle",
			"fading",
			"error",
			"blocked",
			"cancelling",
			"permission_revoked",
			"paste_failed",
		];
		expect(modes.length).toBe(9);
	});
});
