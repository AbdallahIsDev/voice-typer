/**
 * LevelBar a11y tests — covers the ``role="progressbar"`` semantics.
 *
 * The bar exposes its level to assistive tech via ``aria-valuenow``
 * (numeric 0–100) and ``aria-valuetext`` (human-readable "<pct> percent,
 * <tier>" — e.g. "70 percent, loud"). The valuetext is essential for SR
 * users because the raw number alone doesn't convey the qualitative
 * band (silent / low / good / loud) that the visual colour encodes.
 *
 * These tests mount the real ``LevelBar`` with the i18n ``t()`` stubbed
 * out (so the aria-label resolves to the stable catalog key) and assert
 * on the rendered aria attributes.
 */
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LevelBar } from "@/components/feedback/LevelBar";

vi.mock("@/i18n/i18n", () => ({
	t: (key: string) => key,
}));

afterEach(() => {
	cleanup();
});

function getProgressbar(): HTMLElement {
	const el = document.querySelector('[role="progressbar"]');
	if (!el) throw new Error("progressbar element not rendered");
	return el as HTMLElement;
}

describe("LevelBar — aria-valuetext reflects tier", () => {
	it("renders '0 percent, silent' when level is 0", () => {
		render(<LevelBar level={0} playing={false} />);
		const bar = getProgressbar();
		expect(bar.getAttribute("aria-valuenow")).toBe("0");
		expect(bar.getAttribute("aria-valuetext")).toBe("0 percent, silent");
	});

	it("renders '3 percent, low' for a faint signal below the voice threshold", () => {
		// ``getVolumeTier`` classifies anything with peak ≤ 0.05 (and
		// level > 0.005) as "low" — i.e. the user is producing signal
		// but it's too faint for voice detection. Use 0.03 to stay
		// in that band (level=0.15 would cross the 0.05 voice threshold
		// and classify as "good").
		render(<LevelBar level={0.03} playing={false} />);
		const bar = getProgressbar();
		expect(bar.getAttribute("aria-valuenow")).toBe("3");
		expect(bar.getAttribute("aria-valuetext")).toBe("3 percent, low");
	});

	it("renders '45 percent, good' for a healthy signal", () => {
		render(<LevelBar level={0.45} playing={false} />);
		const bar = getProgressbar();
		expect(bar.getAttribute("aria-valuenow")).toBe("45");
		expect(bar.getAttribute("aria-valuetext")).toBe("45 percent, good");
	});

	it("renders '85 percent, loud' for a clipping signal", () => {
		render(<LevelBar level={0.85} playing={false} />);
		const bar = getProgressbar();
		expect(bar.getAttribute("aria-valuenow")).toBe("85");
		expect(bar.getAttribute("aria-valuetext")).toBe("85 percent, loud");
	});

	it("preserves aria-valuemin=0 and aria-valuemax=100", () => {
		render(<LevelBar level={0.5} playing={false} />);
		const bar = getProgressbar();
		expect(bar.getAttribute("aria-valuemin")).toBe("0");
		expect(bar.getAttribute("aria-valuemax")).toBe("100");
	});

	it("switches aria-label to the frozen variant when playing=true", () => {
		render(<LevelBar level={0.5} playing={true} />);
		const bar = getProgressbar();
		expect(bar.getAttribute("aria-label")).toBe(
			"microphone.levelBarFrozenAria",
		);
	});

	it("uses the listening aria-label when playing=false", () => {
		render(<LevelBar level={0.5} playing={false} />);
		const bar = getProgressbar();
		expect(bar.getAttribute("aria-label")).toBe("microphone.levelBarAria");
	});
});

describe("LevelBar — compositor-friendly scaleX fill", () => {
	function getFill(): HTMLElement {
		const fill = getProgressbar().firstElementChild;
		if (!fill) throw new Error("fill element not rendered");
		return fill as HTMLElement;
	}

	it("animates the fill via transform scaleX instead of a percentage width", () => {
		render(<LevelBar level={0.45} playing={false} />);
		const fill = getFill();
		expect(fill.style.transform).toBe("scaleX(0.45)");
		// The old layout-triggering form set an animating width %; it
		// must stay unset so the track width never participates in the
		// animation.
		expect(fill.style.width).toBe("");
	});

	it("clamps negative levels to a fully collapsed (empty) track", () => {
		render(<LevelBar level={-0.2} playing={false} />);
		expect(getFill().style.transform).toBe("scaleX(0)");
	});

	it("keeps the fill pinned to the left edge via transform-origin-left", () => {
		render(<LevelBar level={0.6} playing={false} />);
		expect(getFill().className).toContain("origin-left");
	});

	it("transitions only transform + opacity (no transition-all)", () => {
		render(<LevelBar level={0.3} playing={false} />);
		const cls = getFill().className;
		expect(cls).toContain("transition-[transform,opacity]");
		expect(cls).not.toContain("transition-all");
	});
});
