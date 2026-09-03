/**
 * LastUpdatedIndicator — refresh micro-interaction contract.
 *
 * Pins the fix for the refresh click glitch: while a refresh is in
 * flight the SAME icon spins in place (``animate-spin`` on the
 * unchanged h-3.5 glyph). Swapping in a different element (e.g. a
 * border-2 Spinner at a different box size) reads as a size/color
 * jump on every click; rotating the mounted icon keeps the box,
 * stroke, and color identical so the only motion is the rotation.
 *
 * Also pins that the button's accessible name survives both states.
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@hugeicons/react", () => ({
	HugeiconsIcon: ({
		icon,
		...rest
	}: {
		icon?: { name?: string };
	} & React.HTMLAttributes<HTMLSpanElement>) => (
		<span data-testid="hugeicon" data-name={icon?.name} {...rest} />
	),
}));

vi.mock("@hugeicons/core-free-icons", async () => {
	const { createHugeiconsMock } = await import(
		"@/__tests__/helpers/hugeicons-mock"
	);
	return createHugeiconsMock();
});

import { LastUpdatedIndicator } from "@/components/common/LastUpdatedIndicator";
import { t } from "@/i18n/i18n";

describe("LastUpdatedIndicator refresh visual", () => {
	afterEach(() => {
		cleanup();
	});

	it("idle: refresh icon renders at normal size with no spin and the button keeps its accessible name", () => {
		render(<LastUpdatedIndicator agoLabel="5s ago" onRefresh={() => {}} />);

		const button = screen.getByRole("button", {
			name: t("common.refreshAria"),
		});
		expect(button).toBeTruthy();
		expect(button.hasAttribute("disabled")).toBe(false);

		const icon = screen.getByTestId("hugeicon");
		expect(icon.className).toContain("h-3.5");
		expect(icon.className).toContain("w-3.5");
		expect(icon.className).not.toContain("animate-spin");
	});

	it("refreshing: the SAME icon spins in place — no element swap, no size change, accessible name intact", () => {
		render(
			<LastUpdatedIndicator
				agoLabel="5s ago"
				onRefresh={() => {}}
				refreshing
			/>,
		);

		const button = screen.getByRole("button", {
			name: t("common.refreshAria"),
		});
		expect(button).toBeTruthy();
		expect(button.hasAttribute("disabled")).toBe(true);

		// The icon keeps its box (h-3.5/w-3.5) and only gains rotation —
		// no Spinner (role="img") is mounted in its place.
		const icon = screen.getByTestId("hugeicon");
		expect(icon.className).toContain("h-3.5");
		expect(icon.className).toContain("w-3.5");
		expect(icon.className).toContain("animate-spin");
		expect(screen.queryByRole("img")).toBeNull();
	});

	it("click fires onRefresh when idle", () => {
		const onRefresh = vi.fn();
		render(<LastUpdatedIndicator agoLabel="5s ago" onRefresh={onRefresh} />);

		fireEvent.click(
			screen.getByRole("button", { name: t("common.refreshAria") }),
		);
		expect(onRefresh).toHaveBeenCalledTimes(1);
	});
});
