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

	it("refreshing: the SAME icon spins in place, no element swap, no size change, accessible name intact", () => {
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
