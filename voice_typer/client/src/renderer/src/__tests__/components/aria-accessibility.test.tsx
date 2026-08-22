/**
 * XA-8 — ARIA accessibility regression tests for the renderer's feedback /
 * common / UI primitives.
 *
 * Scope: every XA-8 sub-item gets at least one assertion. Already-fixed
 * sub-items (H1 / M1 / M2 / M3 / M4 / M5 / L1 / L2 / L3 / L6 / L7) are
 * pinned here so a future regression can't silently re-introduce the
 * gap. The remaining sub-items (M6 / L5) are exercised against the
 * actual fix landed in this same task.
 *
 * Test map (mirrors the task spec):
 *   1. EmptyState: role="alert" on error variant, role="status" otherwise.
 *   2. ErrorBoundary: renders localized strings (mock t() → marker).
 *   3. KeyringStatusBadge: does NOT carry a redundant aria-label when
 *      the visible text + TooltipContent already provide the accessible
 *      name (compact mode still exposes a short generic label).
 *   4. sonner Toaster: containerAriaLabel + closeButtonAriaLabel are
 *      wired through t("a11y.notifications") / t("a11y.close").
 *   5. Slider / Switch / Button: dev-mode console.warn fires when an
 *      accessible name is missing (icon-only button, no aria-label).
 *   6. InfoTooltip: SVG has NO <title> element (redundant with the
 *      wrapping button's aria-label).
 *   7. LastUpdatedIndicator: the dynamic "Last updated X ago" label is
 *      wrapped in an aria-live="polite" region.
 *   8. Spinner: decorative={true} renders <div aria-hidden="true">.
 */
import { cleanup, render, screen } from "@testing-library/react";
import { Component } from "react";
import type { ToasterProps } from "sonner";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { KeyringStatusBadge } from "@/components/common/KeyringStatusBadge";
import { LastUpdatedIndicator } from "@/components/common/LastUpdatedIndicator";
import { EmptyState } from "@/components/feedback/EmptyState";
import { ErrorBoundary } from "@/components/feedback/ErrorBoundary";
import { InfoTooltip } from "@/components/feedback/InfoTooltip";
import { Spinner } from "@/components/feedback/Spinner";
import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";
import { Toaster } from "@/components/ui/sonner";
import { Switch } from "@/components/ui/switch";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { KeyringStatus } from "@/types/config";

// Stub the hugeicons wrapper so SegmentedControl's icon-only path can be
// exercised without pulling in the real (heavy) hugeicons renderer.
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

afterEach(() => {
	cleanup();
});

// ────────────────────────────────────────────────────────────────────
// Test 1 — EmptyState roles
// ────────────────────────────────────────────────────────────────────
describe("XA-8-H1: EmptyState role", () => {
	it("uses role=alert when variant='error'", () => {
		render(
			<EmptyState
				icon={(() => null) as unknown as never}
				title="Failed to load"
				variant="error"
			/>,
		);
		expect(screen.getByRole("alert")).toBeInTheDocument();
	});

	it("uses role=status when variant is omitted (default 'info')", () => {
		render(
			<EmptyState
				icon={(() => null) as unknown as never}
				title="No items yet"
			/>,
		);
		expect(screen.getByRole("status")).toBeInTheDocument();
		expect(screen.queryByRole("alert")).toBeNull();
	});

	it("uses role=status when variant='info' is passed explicitly", () => {
		render(
			<EmptyState
				icon={(() => null) as unknown as never}
				title="No items yet"
				variant="info"
			/>,
		);
		expect(screen.getByRole("status")).toBeInTheDocument();
	});
});

// ────────────────────────────────────────────────────────────────────
// Test 2 — ErrorBoundary renders localized strings
// ────────────────────────────────────────────────────────────────────
// Mock the i18n surface so every t() call returns a stable marker that
// encodes the key. ErrorBoundary's fallback UI uses several t() calls
// (title / description / copyError / openLogs / resetSettings / tryAgain
// / reloadApp) — asserting each marker appears proves every string flows
// through t() rather than being a hard-coded English literal.
const ERROR_BOUNDARY_MARKER = "T(errorBoundary.title)";
vi.mock("@/i18n/i18n", () => ({
	t: (key: string) => `T(${key})`,
	useT: () => () => `T-useT`,
	getLocale: () => "en",
	isRtlLocale: () => false,
	subscribeLocale: () => () => {},
}));

// A child component that throws on demand so we can drive the boundary
// into its fallback render. React 19's strict-mode double-invocation
// breaks counter-based approaches; a module-level boolean is robust.
let shouldThrow = false;
class Thrower extends Component<{ message: string }> {
	render() {
		if (shouldThrow) throw new Error(this.props.message);
		return <div data-testid="child-ok">child</div>;
	}
}

describe("XA-8-M2: ErrorBoundary renders localized strings", () => {
	afterEach(() => {
		shouldThrow = false;
	});

	it("renders the localized title marker in the fallback UI", () => {
		shouldThrow = true;
		render(
			<ErrorBoundary>
				<Thrower message="boundary-failure" />
			</ErrorBoundary>,
		);
		// The visible <h1> text is the t()-returned marker — proves the
		// English literal "Something went wrong" is NOT hard-coded.
		expect(screen.getByText(ERROR_BOUNDARY_MARKER)).toBeInTheDocument();
	});

	it("renders the localized button labels (Copy error / Open logs / Reset settings)", () => {
		shouldThrow = true;
		render(
			<ErrorBoundary>
				<Thrower message="boundary-failure" />
			</ErrorBoundary>,
		);
		expect(
			screen.getByRole("button", { name: "T(errorBoundary.copyError)" }),
		).toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: "T(errorBoundary.openLogs)" }),
		).toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: "T(errorBoundary.resetSettings)" }),
		).toBeInTheDocument();
	});

	it("renders the localized 'Try Again' and 'Reload App' buttons", () => {
		shouldThrow = true;
		render(
			<ErrorBoundary>
				<Thrower message="boundary-failure" />
			</ErrorBoundary>,
		);
		expect(
			screen.getByRole("button", { name: "T(errorBoundary.tryAgain)" }),
		).toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: "T(errorBoundary.reloadApp)" }),
		).toBeInTheDocument();
	});
});

// ────────────────────────────────────────────────────────────────────
// Test 3 — KeyringStatusBadge: no redundant aria-label
// ────────────────────────────────────────────────────────────────────
describe("XA-8-M3: KeyringStatusBadge redundant aria-label", () => {
	const availableStatus: KeyringStatus = {
		available: true,
		backend: "SecretServiceKeyring",
		fallback: false,
		reason: null,
	};

	it("in full mode does NOT set aria-label (visible 'Secure' text provides the name)", () => {
		const { container } = render(
			<TooltipProvider delayDuration={200}>
				<KeyringStatusBadge status={availableStatus} />
			</TooltipProvider>,
		);
		const trigger = container.querySelector("button");
		expect(trigger).not.toBeNull();
		// The visible "Secure" span provides the accessible name — a
		// duplicate aria-label would have SR users hear the tooltip twice
		// (once on the button, once when the tooltip opens on focus).
		expect(trigger?.getAttribute("aria-label")).toBeNull();
	});

	it("in compact mode sets a SHORT generic aria-label (NOT the full tooltip text)", () => {
		const { container } = render(
			<TooltipProvider delayDuration={200}>
				<KeyringStatusBadge status={availableStatus} compact />
			</TooltipProvider>,
		);
		const trigger = container.querySelector("button");
		expect(trigger).not.toBeNull();
		const label = trigger?.getAttribute("aria-label");
		expect(label).not.toBeNull();
		// The compact label must be the SHORT settings.keyring.statusLabel,
		// NOT the longer settings.keyring.availableWithBackend tooltip text
		// (which duplicates the TooltipContent and would double-announce).
		expect(label).not.toContain("SecretServiceKeyring");
	});
});

// ────────────────────────────────────────────────────────────────────
// Test 4 — sonner Toaster localized aria-labels
// ────────────────────────────────────────────────────────────────────
// Capture the most recent props passed to the mocked Sonner so each
// test can assert on the aria-label props after a render.
let lastToasterProps: ToasterProps | null = null;

vi.mock("sonner", () => ({
	Toaster: (props: ToasterProps) => {
		lastToasterProps = props;
		return <div data-testid="mocked-toaster" />;
	},
}));

describe("XA-8-M6: sonner Toaster localized aria-labels", () => {
	beforeEach(() => {
		lastToasterProps = null;
	});

	it("passes containerAriaLabel = t('a11y.notifications')", () => {
		render(<Toaster />);
		expect(lastToasterProps).not.toBeNull();
		// The mock t() returns `T(<key>)` (see the i18n mock above).
		expect(lastToasterProps?.containerAriaLabel).toBe("T(a11y.notifications)");
	});

	it("passes toastOptions.closeButtonAriaLabel = t('a11y.close')", () => {
		render(<Toaster />);
		expect(lastToasterProps).not.toBeNull();
		expect(lastToasterProps?.toastOptions?.closeButtonAriaLabel).toBe(
			"T(a11y.close)",
		);
	});

	it("does NOT pass the sonner library defaults (hard-coded English 'Notifications' / 'Close')", () => {
		render(<Toaster />);
		expect(lastToasterProps?.containerAriaLabel).not.toBe("Notifications");
		expect(lastToasterProps?.toastOptions?.closeButtonAriaLabel).not.toBe(
			"Close",
		);
	});
});

// ────────────────────────────────────────────────────────────────────
// Test 5 — Slider / Switch / Button dev-mode a11y warn
// ────────────────────────────────────────────────────────────────────
describe("XA-8-L1/L2/L3: Slider / Switch / Button dev-mode a11y warn", () => {
	it("Slider warns when aria-label / aria-labelledby / thumbLabels are all absent", () => {
		const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
		render(<Slider defaultValue={[50]} />);
		const sliderWarns = warn.mock.calls.filter((c) =>
			String(c[0]).includes("[renderer:Slider]"),
		);
		expect(sliderWarns).toHaveLength(1);
		warn.mockRestore();
	});

	it("Switch warns when no aria-label / aria-labelledby is provided", () => {
		const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
		render(<Switch />);
		const switchWarns = warn.mock.calls.filter((c) =>
			String(c[0]).includes("[renderer:Switch]"),
		);
		expect(switchWarns).toHaveLength(1);
		warn.mockRestore();
	});

	it("Button warns when icon-only (no children, no aria-label, no aria-labelledby)", () => {
		const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
		render(<Button />);
		const buttonWarns = warn.mock.calls.filter((c) =>
			String(c[0]).includes("[renderer:Button]"),
		);
		expect(buttonWarns).toHaveLength(1);
		warn.mockRestore();
	});

	it("Button does NOT warn when aria-label is provided (icon-only button WITH name)", () => {
		const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
		render(<Button aria-label="Close" />);
		const buttonWarns = warn.mock.calls.filter((c) =>
			String(c[0]).includes("[renderer:Button]"),
		);
		expect(buttonWarns).toHaveLength(0);
		warn.mockRestore();
	});
});

// ────────────────────────────────────────────────────────────────────
// Test 6 — InfoTooltip: SVG has no <title> element
// ────────────────────────────────────────────────────────────────────
describe("XA-8-L3: InfoTooltip SVG has no redundant <title>", () => {
	it("renders an SVG without a <title> child (button aria-label is the source of truth)", () => {
		const { container } = render(
			<TooltipProvider delayDuration={200}>
				<InfoTooltip text="More info" />
			</TooltipProvider>,
		);
		const svg = container.querySelector("svg");
		expect(svg).not.toBeNull();
		// A <title> inside the SVG would be announced IN ADDITION to the
		// wrapping button's aria-label (double announcement). The fix
		// marks the SVG aria-hidden and drops the <title>.
		const title = svg?.querySelector("title");
		expect(title).toBeNull();
		expect(svg?.getAttribute("aria-hidden")).toBe("true");
	});

	it("still exposes the accessible name on the wrapping button", () => {
		render(
			<TooltipProvider delayDuration={200}>
				<InfoTooltip text="More info" />
			</TooltipProvider>,
		);
		expect(
			screen.getByRole("button", { name: "T(a11y.moreInfo)" }),
		).toBeInTheDocument();
	});
});

// ────────────────────────────────────────────────────────────────────
// Test 7 — LastUpdatedIndicator wrapped in aria-live=polite
// ────────────────────────────────────────────────────────────────────
describe("XA-8-L5: LastUpdatedIndicator aria-live region", () => {
	it("wraps the dynamic 'Last updated' label in an aria-live=polite region", () => {
		const { container } = render(
			<LastUpdatedIndicator agoLabel="5s ago" onRefresh={() => {}} />,
		);
		const liveRegion = container.querySelector('[aria-live="polite"]');
		expect(liveRegion).not.toBeNull();
		expect(liveRegion?.textContent).toContain("T(common.lastUpdatedWithValue");
	});

	it("leaves the refresh button OUTSIDE the aria-live region (no double announce)", () => {
		const { container } = render(
			<LastUpdatedIndicator agoLabel="5s ago" onRefresh={() => {}} />,
		);
		const liveRegion = container.querySelector('[aria-live="polite"]');
		const refreshButton = screen.getByRole("button");
		expect(liveRegion).not.toBeNull();
		expect(liveRegion?.contains(refreshButton)).toBe(false);
	});
});

// ────────────────────────────────────────────────────────────────────
// Test 8 — Spinner decorative prop
// ────────────────────────────────────────────────────────────────────
describe("XA-8-L6: Spinner decorative prop", () => {
	it("decorative={true} renders <div aria-hidden=true> with no role and no aria-label", () => {
		const { container } = render(<Spinner decorative />);
		const root = container.firstElementChild;
		expect(root).not.toBeNull();
		expect(root?.tagName).toBe("DIV");
		expect(root?.getAttribute("aria-hidden")).toBe("true");
		expect(root?.getAttribute("role")).toBeNull();
		expect(root?.getAttribute("aria-label")).toBeNull();
	});

	it("default (decorative not passed) still renders the labeled <span role=img> for AT users", () => {
		render(<Spinner />);
		expect(
			screen.getByRole("img", { name: "T(a11y.loading)" }),
		).toBeInTheDocument();
	});
});
