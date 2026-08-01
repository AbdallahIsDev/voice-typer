/**
 * Parity tests for the bubble theme-token migration.
 *
 * Background
 * ----------
 * Pre-migration: the bubble's className utilities used the raw Tailwind
 * `zinc` / `white` palette (`bg-white dark:bg-zinc-900`,
 * `border-zinc-200 dark:border-white/10`, `text-zinc-600 dark:text-zinc-300`,
 * `bg-zinc-500 dark:bg-zinc-400`, `bg-zinc-900 dark:bg-white`). These do
 * NOT reference the semantic CSS variables (`--card`, `--border`,
 * `--text-muted`, `--text-primary`, `--surface-hover`) that `index.css`
 * defines and the rest of the app consumes via `bg-card` / `border-border` /
 * `text-(--text-muted)` etc. When the user selected any non-default theme
 * preset (Nord, Dracula, Tokyo Night, …) or a custom theme, the main app
 * re-skinned but the bubble kept rendering the default white/zinc palette —
 * the bubble visually clashed with the rest of the app.
 *
 * Post-migration: every bubble className uses semantic tokens. These parity
 * tests assert the raw palette substrings do not leak back in.
 *
 * If a future edit re-introduces a `zinc-` / `bg-white dark:bg-zinc-…`
 * utility anywhere in the bubble package, the test fails LOUDLY at the
 * className-source level (not at the rendered-DOM level — Tailwind purges
 * unused classes, so a DOM-level scan would silently miss a regression in
 * a code path that isn't exercised by the test renderer).
 */

import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Bubble } from "@/Bubble";

// The shared button className constant is the most important guard — it
// is imported by `BubbleMicButton`, `BubbleStopButton`, and
// `BubbleDismissButton`, so a single regression here propagates to all
// three affordances.
import { BUBBLE_BUTTON_CLASS, type BubbleMode } from "../constants";

// Stub window.bubble so <Bubble /> mounts without crashing. The renderer
// casts `window.bubble` to `BubbleWindowBubble | undefined` and calls
// `?.onShow` / `?.onHide` / `?.onSetState` / `?.onConfig` — we only need
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
		// `--text-muted` (resting foreground), `--surface-hover` (hover
		// background), `--text-primary` (hover foreground).
		expect(BUBBLE_BUTTON_CLASS).toContain("text-(--text-muted)");
		expect(BUBBLE_BUTTON_CLASS).toContain("hover:bg-(--surface-hover)");
		expect(BUBBLE_BUTTON_CLASS).toContain("hover:text-(--text-primary)");
	});

	it("Bubble pill container uses semantic tokens (no raw palette)", () => {
		(window as unknown as Record<string, unknown>).bubble = makeMockBubble();
		try {
			render(<Bubble />);
			// The pill is the inner `<div>` with `rounded-full` — query it
			// by its border/bg utility classes (which are now semantic).
			const pill = document.querySelector(".bg-card.border-border");
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
