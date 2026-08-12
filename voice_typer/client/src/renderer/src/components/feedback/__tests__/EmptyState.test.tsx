/**
 * EmptyState unit tests.
 *
 * : EmptyState's title was previously rendered as a <p>, which
 * meant screen-reader users couldn't navigate empty-state cards by
 * heading (the H key in NVDA / VoiceOver rotor). The title is now an
 * <h3> so it sits below the typical page <h1>/<h2> hierarchy and SR
 * users can jump to it.
 *
 *  (related): the destructure ``icon: _icon`` underscore-prefix
 * misuse (the variable WAS used at the icon={...} site, contradicting
 * the convention that underscore-prefixed names are intentionally
 * unused) was renamed back to plain ``icon`` so the code matches its
 * actual usage.
 */
import { AlertCircleIcon, Mic02Icon } from "@hugeicons/core-free-icons";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { EmptyState } from "@/components/feedback/EmptyState";

// Mock the real HugeiconsIcon so the test doesn't depend on the SVG
// rendering pipeline. The mock mirrors the pattern used in other test
// files (Settings-empty-state.test.tsx, etc.) and exposes the icon's
// `name` via data-name so individual tests can assert which icon was
// rendered.
vi.mock("@hugeicons/react", () => ({
	HugeiconsIcon: ({
		children,
		icon,
	}: {
		children?: React.ReactNode;
		icon?: { name?: string };
	}) => (
		<span data-testid="hugeicon" data-name={icon?.name}>
			{children}
		</span>
	),
}));

vi.mock("@hugeicons/core-free-icons", async () => {
	const { createHugeiconsMock } = await import(
		"@/__tests__/helpers/hugeicons-mock"
	);
	return createHugeiconsMock();
});

describe("EmptyState — BG-R13 (title as <h3> + icon prop wiring)", () => {
	afterEach(() => {
		cleanup();
	});

	it("renders the title as an <h3> heading (not a <p>) so SR users can navigate by heading", () => {
		render(<EmptyState icon={Mic02Icon} title="No dictations yet" />);
		// testing-library's role="heading" matcher with the level option
		// is the canonical way to assert heading semantics.
		const heading = screen.getByRole("heading", { level: 3 });
		expect(heading).toBeInTheDocument();
		expect(heading.tagName).toBe("H3");
		expect(heading).toHaveTextContent("No dictations yet");
	});

	it("renders the icon prop (verifies _icon -> icon rename is wired correctly)", () => {
		render(<EmptyState icon={Mic02Icon} title="No dictations yet" />);
		const icons = screen.getAllByTestId("hugeicon");
		// The Mic02Icon is the first icon rendered (the title-row icon).
		expect(icons.length).toBeGreaterThanOrEqual(1);
		expect(icons[0]?.getAttribute("data-name")).toBe("Mic02Icon");
	});

	it("renders the Alert02Icon in the title-row when variant='error' (overrides the icon prop)", () => {
		render(
			<EmptyState
				icon={Mic02Icon}
				title="Failed to load vocabulary"
				variant="error"
			/>,
		);
		const icons = screen.getAllByTestId("hugeicon");
		expect(icons.length).toBeGreaterThanOrEqual(1);
		// The error variant hard-codes Alert02Icon for the title-row icon
		// (independent of the `icon` prop) so the destructive visual
		// treatment is consistent across all error empty-states.
		expect(icons[0]?.getAttribute("data-name")).toBe("Alert02Icon");
	});

	it("renders the description as a <p> below the title", () => {
		render(
			<EmptyState
				icon={Mic02Icon}
				title="No dictations yet"
				description="Press the mic button to start your first recording."
			/>,
		);
		const description = screen.getByText(
			"Press the mic button to start your first recording.",
		);
		expect(description.tagName).toBe("P");
	});

	it("renders the action button with its label when both actionLabel and onAction are provided", () => {
		const onAction = vi.fn();
		render(
			<EmptyState
				icon={Mic02Icon}
				title="No templates yet"
				actionLabel="Add template"
				onAction={onAction}
			/>,
		);
		const button = screen.getByRole("button", { name: /Add template/ });
		expect(button).toBeInTheDocument();
		fireEvent.click(button);
		expect(onAction).toHaveBeenCalledTimes(1);
	});

	it("does NOT render the action button when actionLabel is provided without onAction (or vice versa)", () => {
		render(
			<EmptyState
				icon={Mic02Icon}
				title="No templates yet"
				actionLabel="Add template"
				// onAction intentionally omitted
			/>,
		);
		expect(screen.queryByRole("button")).toBeNull();
	});

	it("uses AlertCircleIcon-style error variant correctly with the alert role", () => {
		// Sanity: error variant sets role="alert" on the container.
		render(
			<EmptyState
				icon={AlertCircleIcon}
				title="Failed to load"
				variant="error"
			/>,
		);
		const alert = screen.getByRole("alert");
		expect(alert).toBeInTheDocument();
		// The title is still rendered as an <h3> inside the alert region.
		const heading = screen.getByRole("heading", { level: 3 });
		expect(heading).toHaveTextContent("Failed to load");
	});
	it("forwards actionRef to the action button so callers can focus it without querySelector", () => {
		const ref = { current: null } as React.RefObject<HTMLButtonElement | null>;
		render(
			<EmptyState
				icon={Mic02Icon}
				title="Failed to load"
				actionLabel="Retry"
				onAction={vi.fn()}
				actionRef={ref}
			/>,
		);
		const button = screen.getByRole("button", { name: /Retry/ });
		expect(ref.current).toBe(button);
	});

	it("does NOT render the action button (and ignores actionRef) when actionLabel is missing", () => {
		const ref = { current: null } as React.RefObject<HTMLButtonElement | null>;
		render(
			<EmptyState icon={Mic02Icon} title="No items yet" actionRef={ref} />,
		);
		expect(screen.queryByRole("button")).toBeNull();
		expect(ref.current).toBeNull();
	});
});
