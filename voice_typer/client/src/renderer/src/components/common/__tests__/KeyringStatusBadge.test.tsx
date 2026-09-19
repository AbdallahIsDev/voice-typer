import {
	cleanup,
	fireEvent,
	render,
	screen,
	waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { KeyringStatusBadge } from "@/components/common/KeyringStatusBadge";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { KeyringStatus } from "@/types/config";

// Stub the hugeicons wrapper so we don't pull in the real SVG renderer.
vi.mock("@hugeicons/react", () => ({
	HugeiconsIcon: ({ icon }: { icon?: { name?: string } }) => (
		<span data-testid="hugeicon" data-name={icon?.name} />
	),
}));

vi.mock("@hugeicons/core-free-icons", async () => {
	const { createHugeiconsMock } = await import(
		"@/__tests__/helpers/hugeicons-mock"
	);
	return createHugeiconsMock();
});

const availableStatus: KeyringStatus = {
	available: true,
	backend: "SecretServiceKeyring",
	fallback: false,
	reason: null,
};

const fallbackStatus: KeyringStatus = {
	available: false,
	backend: null,
	fallback: true,
	reason: "keyring.backend.missing",
};

/** Wrap a node in the shared TooltipProvider so isolated tests can mount. */
function withProvider(node: React.ReactNode) {
	return <TooltipProvider delayDuration={200}>{node}</TooltipProvider>;
}

describe("KeyringStatusBadge, BG-R11 (branching + cursor-default)", () => {
	afterEach(() => {
		cleanup();
	});

	it("available + full: shows 'Secure' text with green LockKey icon", () => {
		render(withProvider(<KeyringStatusBadge status={availableStatus} />));
		// en.json: settings.keyring.secure → "Secure"
		expect(screen.getByText("Secure")).toBeInTheDocument();
		// LockKeyIcon used in the available branch.
		expect(screen.getByTestId("hugeicon")).toHaveAttribute(
			"data-name",
			"LockKeyIcon",
		);
	});

	it("available + compact: icon-only, no visible text, with aria-label", () => {
		render(
			withProvider(<KeyringStatusBadge status={availableStatus} compact />),
		);
		// No visible "Secure" text in compact mode.
		expect(screen.queryByText("Secure")).toBeNull();
		// Button still has an accessible name via aria-label.
		const btn = screen.getByRole("button");
		expect(btn).toHaveAttribute("aria-label");
	});

	it("available + full: aria-label is omitted (visible text provides accessible name)", () => {
		render(withProvider(<KeyringStatusBadge status={availableStatus} />));
		const btn = screen.getByRole("button");
		expect(btn).not.toHaveAttribute("aria-label");
	});

	it("fallback + full: shows 'Plaintext' text with amber Alert02 icon", () => {
		render(withProvider(<KeyringStatusBadge status={fallbackStatus} />));
		// en.json: settings.keyring.plaintext → "Plaintext"
		expect(screen.getByText("Plaintext")).toBeInTheDocument();
		expect(screen.getByTestId("hugeicon")).toHaveAttribute(
			"data-name",
			"Alert02Icon",
		);
	});

	it("fallback + compact: icon-only, with aria-label", () => {
		render(
			withProvider(<KeyringStatusBadge status={fallbackStatus} compact />),
		);
		expect(screen.queryByText("Plaintext")).toBeNull();
		const btn = screen.getByRole("button");
		expect(btn).toHaveAttribute("aria-label");
	});

	it("undefined status → fallback branch (legacy responses treated as fallback)", () => {
		// KeyringStatusBadge is called without `status` for legacy
		// responses that don't carry the keyring_status field.
		// We must never claim keyring is available when we don't
		// know, the badge should render the fallback UI.
		render(withProvider(<KeyringStatusBadge />));
		expect(screen.getByText("Plaintext")).toBeInTheDocument();
		expect(screen.getByTestId("hugeicon")).toHaveAttribute(
			"data-name",
			"Alert02Icon",
		);
	});

	it("available + tooltip with backend name appears when trigger is focused", async () => {
		render(withProvider(<KeyringStatusBadge status={availableStatus} />));
		const btn = screen.getByRole("button");
		// Radix Tooltip opens on focus / pointer enter (not on click).
		// Fire both events to be robust against Radix version changes.
		btn.focus();
		fireEvent.mouseEnter(btn);
		fireEvent.pointerEnter(btn);
		// The tooltip text is rendered into a portal at document.body.
		// The availableWithBackend template includes the backend
		// name ("SecretServiceKeyring"). Radix renders the tooltip
		// text twice (once visible + once in an SR-only duplicate)
		// so we use getAllByText and assert >=1 match.
		await waitFor(() => {
			expect(
				screen.getAllByText(/SecretServiceKeyring/).length,
			).toBeGreaterThan(0);
		});
	});

	it("fallback + tooltip with reason appears when trigger is focused", async () => {
		render(withProvider(<KeyringStatusBadge status={fallbackStatus} />));
		const btn = screen.getByRole("button");
		btn.focus();
		fireEvent.mouseEnter(btn);
		fireEvent.pointerEnter(btn);
		await waitFor(() => {
			expect(
				screen.getAllByText(/keyring\.backend\.missing/).length,
			).toBeGreaterThan(0);
		});
	});

	it("BG-R11: trigger button does NOT carry cursor-help (misleading affordance)", () => {
		const { rerender } = render(
			withProvider(<KeyringStatusBadge status={availableStatus} />),
		);
		const btn = screen.getByRole("button");
		expect(btn.className).not.toContain("cursor-help");
		expect(btn.className).toContain("cursor-default");

		// Re-render with compact + fallback variant and check the
		// same invariant, cursor-help removal applies to ALL branches.
		rerender(
			withProvider(<KeyringStatusBadge status={fallbackStatus} compact />),
		);
		const btn2 = screen.getByRole("button");
		expect(btn2.className).not.toContain("cursor-help");
		expect(btn2.className).toContain("cursor-default");
	});

	it("ZU-32: does NOT mount its own <TooltipProvider data-slot='tooltip-provider'> (per-caller provider removed)", () => {
		// provider can own delayDuration / skipDelayDuration for the
		// whole tree. The wrapper here supplies the provider; the
		// component itself should not render a second one.
		const { container } = render(
			withProvider(<KeyringStatusBadge status={availableStatus} />),
		);
		const innerProviders = container.querySelectorAll(
			'[data-slot="tooltip-provider"]',
		);
		expect(innerProviders.length).toBe(0);
	});

	it("XA-3: trigger button uses the shared focusRing (ring-1, not the thinner /50 alpha)", () => {
		// Routes through the shared `focusRing` so the badge matches the
		// design-system Button (C-FOCUS-2: full-opacity ring-1 / ring-ring).
		const { rerender } = render(
			withProvider(<KeyringStatusBadge status={availableStatus} />),
		);
		const btn = screen.getByRole("button");
		const cls = btn.className;
		expect(cls).toContain("focus-visible:ring-1");
		expect(cls).toContain("focus-visible:ring-ring");
		// Legacy /50 alpha modifier (WCAG 1.4.11) must be gone.
		expect(cls).not.toMatch(/focus-visible:ring-ring\/50/);

		rerender(withProvider(<KeyringStatusBadge status={fallbackStatus} />));
		const btn2 = screen.getByRole("button");
		const cls2 = btn2.className;
		expect(cls2).toContain("focus-visible:ring-1");
		expect(cls2).toContain("focus-visible:ring-ring");
		expect(cls2).not.toMatch(/focus-visible:ring-ring\/50/);
	});
});
