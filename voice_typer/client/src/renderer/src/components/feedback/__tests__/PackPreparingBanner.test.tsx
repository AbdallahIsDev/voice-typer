/**
 * Tests for `PackPreparingBanner`.
 *
 * Coverage:
 *   - renders nothing when `visible === false` (parent layout doesn't
 *     reserve space)
 *   - renders the "Preparing offline engine…" copy from
 *     `t("pack.preparingOfflineEngine")` when visible
 *   - exposes `role="status"` + `aria-live="polite"` so screen readers
 *     announce the message once when it appears (NOT assertive — the
 *     message is informational, not an error)
 *   - exposes `data-pack-status` so integration tests can assert on
 *     the underlying PackStatus without parsing visible text
 *   - aria-label is wired through `t("pack.preparingOfflineEngineAria", { status })`
 *     so AT users get the diagnostic context
 *   - the `className` prop merges with the base classes (tailwind-merge)
 *
 * Strategy: render the presentational component with the i18n `t()`
 * stubbed to return the key (so the test asserts on stable strings).
 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PackPreparingBanner } from "@/components/feedback/PackPreparingBanner";
import type { PackStatus } from "@/hooks/usePackDownload";

// Mock i18n so we don't load the real locale chunks in unit tests.
// The mock returns the key as the translated string (with `: key=value`
// suffixes for placeholder substitutions that don't match a `{placeholder}`
// in the key — so we can assert on both the bare key and the param
// propagation).
vi.mock("@/i18n/i18n", () => ({
	t: (key: string, params?: Record<string, string>) => {
		if (!params) return key;
		let result = key;
		const leftover: string[] = [];
		for (const [k, v] of Object.entries(params)) {
			const placeholder = `{${k}}`;
			if (result.includes(placeholder)) {
				result = result.replace(placeholder, String(v));
			} else {
				leftover.push(`${k}=${String(v)}`);
			}
		}
		if (leftover.length > 0) {
			result = `${result}: ${leftover.join(", ")}`;
		}
		return result;
	},
}));

afterEach(() => {
	cleanup();
});

describe("PackPreparingBanner — visibility", () => {
	it("renders nothing when visible is false", () => {
		const { container } = render(
			<PackPreparingBanner visible={false} status="downloading" />,
		);
		expect(container.firstElementChild).toBeNull();
	});

	it("renders the banner when visible is true", () => {
		render(<PackPreparingBanner visible={true} status="downloading" />);
		expect(screen.getByText("pack.preparingOfflineEngine")).toBeInTheDocument();
	});
});

describe("PackPreparingBanner — a11y", () => {
	it("uses role=status so screen readers treat it as a live region", () => {
		render(<PackPreparingBanner visible={true} status="downloading" />);
		const region = screen.getByRole("status");
		expect(region).toBeInTheDocument();
	});

	it("carries aria-live=polite (NOT assertive — informational, not an error)", () => {
		render(<PackPreparingBanner visible={true} status="verifying" />);
		const region = screen.getByRole("status");
		expect(region.getAttribute("aria-live")).toBe("polite");
	});

	it("aria-label includes the status via the i18n placeholder", () => {
		render(<PackPreparingBanner visible={true} status="corrupt" />);
		const region = screen.getByRole("status");
		// The mock t() returns the key with `{status}` substituted, so
		// the label is the i18n key with `corrupt` interpolated.
		expect(region.getAttribute("aria-label")).toContain(
			"pack.preparingOfflineEngineAria",
		);
		expect(region.getAttribute("aria-label")).toContain("corrupt");
	});
});

describe("PackPreparingBanner — data-pack-status", () => {
	const statuses: PackStatus[] = [
		"idle",
		"downloading",
		"verifying",
		"ready",
		"failed",
		"missing",
		"corrupt",
		"worker-starting",
		"worker-crashed",
		"worker-unloaded",
	];

	for (const status of statuses) {
		it(`exposes data-pack-status="${status}"`, () => {
			render(<PackPreparingBanner visible={true} status={status} />);
			const region = screen.getByRole("status");
			expect(region.getAttribute("data-pack-status")).toBe(status);
		});
	}

	it("does NOT render the data-pack-status attribute when invisible", () => {
		render(<PackPreparingBanner visible={false} status="downloading" />);
		expect(document.querySelector("[data-pack-status]")).toBeNull();
	});
});

describe("PackPreparingBanner — className merge", () => {
	it("merges consumer className with the base classes (tailwind-merge)", () => {
		render(
			<PackPreparingBanner
				visible={true}
				status="downloading"
				// `mt-2` is additive (no conflict with base) — preserved.
				// `text-amber-600` conflicts with base `text-(--text-muted)` —
				// tailwind-merge drops the base colour so the consumer
				// override wins (this is the same pattern `Spinner` uses
				// for `border-current` overriding `border-accent`).
				className="mt-2 text-amber-600"
			/>,
		);
		const region = screen.getByRole("status");
		expect(region.className).toContain("text-amber-600");
		expect(region.className).toContain("mt-2");
		// Non-conflicting base classes are preserved.
		expect(region.className).toContain("animate-fade-in");
		expect(region.className).toContain("block");
		expect(region.className).toContain("text-[13px]");
		// The conflicting base text-colour is dropped in favour of the
		// consumer override (tailwind-merge semantics).
		expect(region.className).not.toContain("text-(--text-muted)");
	});

	it("preserves all base classes when no consumer className is supplied", () => {
		render(<PackPreparingBanner visible={true} status="downloading" />);
		const region = screen.getByRole("status");
		expect(region.className).toContain("text-(--text-muted)");
		expect(region.className).toContain("animate-fade-in");
		expect(region.className).toContain("block");
		expect(region.className).toContain("text-[13px]");
	});
});
