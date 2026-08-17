/**
 * Behavioral tests for QuickInfoCard (Analytics secondary row + Current
 * Setup section).
 *
 * POLISH round: the card's text block stretches to the card height
 * (items-stretch) and the value carries `mt-auto`, giving the secondary
 * row the same top-pinned label / bottom-pushed number rhythm as the
 * top-row stat cards.
 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { hugeiconsReactMock } from "@/__tests__/helpers/stableMocks";

vi.mock("@hugeicons/react", () => hugeiconsReactMock());

import { QuickInfoCard } from "@/components/dashboard/QuickInfoCard";

afterEach(() => {
	cleanup();
});

// The real icon is an SVG path array; the mocked HugeiconsIcon only
// reads `icon?.name`, so a tagged object suffices here.
const TEST_ICON = { name: "TextIcon" } as unknown as Parameters<
	typeof QuickInfoCard
>[0]["icon"];

describe("QuickInfoCard", () => {
	it("renders the icon, label, value and optional sublabel", () => {
		render(
			<QuickInfoCard
				icon={TEST_ICON}
				label="Avg chars / dictation"
				value="1,234"
				sublabel="12% of dictations"
			/>,
		);
		expect(screen.getByTestId("hugeicon")).toHaveAttribute(
			"data-name",
			"TextIcon",
		);
		expect(screen.getByText("Avg chars / dictation")).toBeInTheDocument();
		expect(screen.getByText("1,234")).toBeInTheDocument();
		expect(screen.getByText("12% of dictations")).toBeInTheDocument();
	});

	it("omits the sublabel line when none is provided", () => {
		render(
			<QuickInfoCard icon={TEST_ICON} label="Longest session" value="4m 30s" />,
		);
		expect(screen.queryByText(/of dictations/)).not.toBeInTheDocument();
	});

	it("stretches the text block and pushes the value down with mt-auto", () => {
		const { container } = render(
			<QuickInfoCard icon={TEST_ICON} label="Corrections" value="3" />,
		);
		const card = container.firstElementChild as HTMLElement;
		expect(card).toHaveClass("flex", "items-stretch");
		const value = screen.getByText("3");
		expect(value.classList.contains("mt-auto")).toBe(true);
		// The value's parent is the vertical flex stack inside the card.
		const stack = value.parentElement as HTMLElement;
		expect(stack).toHaveClass("flex", "flex-col");
	});

	it("applies the muted styling for the Current Setup section", () => {
		const { container } = render(
			<QuickInfoCard muted icon={TEST_ICON} label="Model" value="Tiny" />,
		);
		const card = container.firstElementChild as HTMLElement;
		expect(card.className).toMatch(/bg-\(--bg-subtle\)\/50/);
		expect(card.className).toMatch(/p-3\b/);
	});
});
