/**
 * Behavioral tests for DashboardStatCard (Analytics top-row stat cards).
 *
 * POLISH round: the value carries `mt-auto` so the number is pushed to
 * the bottom of the min-h-24 card, keeping the icon+label row pinned at
 * the top with breathing room between them. The trailing sublabels were
 * pruned at the call sites (Dashboard.tsx) — this file verifies the card
 * itself still renders sublabel/trend when a caller supplies them.
 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { hugeiconsReactMock } from "@/__tests__/helpers/stableMocks";

vi.mock("@hugeicons/react", () => hugeiconsReactMock());

import { DashboardStatCard } from "@/components/dashboard/DashboardStatCard";

afterEach(() => {
	cleanup();
});

// The real icon is an SVG path array; the mocked HugeiconsIcon only
// reads `icon?.name`, so a tagged object suffices here.
const TEST_ICON = { name: "SpeechToTextIcon" } as unknown as Parameters<
	typeof DashboardStatCard
>[0]["icon"];

function renderCard(props: {
	label?: string;
	value?: string;
	sublabel?: string;
	trend?: { pct: number; up: boolean };
}) {
	return render(
		<DashboardStatCard
			label={props.label ?? "Recording Time"}
			value={props.value ?? "1h 12m"}
			icon={TEST_ICON}
			sublabel={props.sublabel}
			trend={props.trend}
		/>,
	);
}

describe("DashboardStatCard", () => {
	it("renders the icon, label and main value", () => {
		renderCard({ label: "Active Days", value: "12" });
		expect(screen.getByText("Active Days")).toBeInTheDocument();
		expect(screen.getByText("12")).toBeInTheDocument();
		expect(screen.getByTestId("hugeicon")).toHaveAttribute(
			"data-name",
			"SpeechToTextIcon",
		);
	});

	it("renders the sublabel only when a caller provides one", () => {
		const { rerender } = renderCard({ value: "12" });
		expect(screen.queryByText("5-day streak")).not.toBeInTheDocument();

		rerender(
			<DashboardStatCard
				label="Active Days"
				value="12"
				icon={TEST_ICON}
				sublabel="5-day streak"
			/>,
		);
		expect(screen.getByText("5-day streak")).toBeInTheDocument();
	});

	it("renders the trend indicator with the localized aria-label", () => {
		renderCard({ trend: { pct: 20, up: true } });
		expect(screen.getByText("▲")).toBeInTheDocument();
		expect(screen.getByText("20%")).toBeInTheDocument();
		expect(screen.getByRole("img")).toHaveAccessibleName(
			"20% more than the previous period",
		);
	});

	it("pins the label row at the top and pushes the value down (min-height + mt-auto)", () => {
		const { container } = renderCard({});
		const card = container.firstElementChild as HTMLElement;
		// The card carries a minimum height so the auto margin has room
		// to spread even when the row's tallest card is only as tall as
		// its content.
		expect(card).toHaveClass("flex", "min-h-24", "flex-col");
		// The main number owns the auto top margin that separates it
		// from the icon+label row above.
		const value = screen.getByText("1h 12m");
		expect(value.classList.contains("mt-auto")).toBe(true);
	});
});
