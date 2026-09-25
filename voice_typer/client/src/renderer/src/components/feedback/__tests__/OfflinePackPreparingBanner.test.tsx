import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { OfflinePackPreparingBanner } from "@/components/feedback/OfflinePackPreparingBanner";
import type { OfflinePackStatus } from "@/hooks/useOfflinePackDownload";

// Mock i18n so we don't load the real locale chunks in unit tests.
// The mock returns the key as the translated string (with `: key=value`
// suffixes for placeholder substitutions that don't match a `{placeholder}`
// in the key, so we can assert on both the bare key and the param
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

describe("OfflinePackPreparingBanner, visibility", () => {
	it("renders nothing when visible is false", () => {
		const { container } = render(
			<OfflinePackPreparingBanner visible={false} status="downloading" />,
		);
		expect(container.firstElementChild).toBeNull();
	});

	it("renders the banner when visible is true", () => {
		render(<OfflinePackPreparingBanner visible={true} status="downloading" />);
		expect(screen.getByText("pack.preparingOfflineEngine")).toBeInTheDocument();
	});
});

describe("OfflinePackPreparingBanner, a11y", () => {
	it("uses role=status so screen readers treat it as a live region", () => {
		render(<OfflinePackPreparingBanner visible={true} status="downloading" />);
		const region = screen.getByRole("status");
		expect(region).toBeInTheDocument();
	});

	it("carries aria-live=polite (NOT assertive, informational, not an error)", () => {
		render(<OfflinePackPreparingBanner visible={true} status="verifying" />);
		const region = screen.getByRole("status");
		expect(region.getAttribute("aria-live")).toBe("polite");
	});

	it("aria-label includes the status via the i18n placeholder", () => {
		render(<OfflinePackPreparingBanner visible={true} status="corrupt" />);
		const region = screen.getByRole("status");
		// The mock t() returns the key with `{status}` substituted, so
		// the label is the i18n key with `corrupt` interpolated.
		expect(region.getAttribute("aria-label")).toContain(
			"pack.preparingOfflineEngineAria",
		);
		expect(region.getAttribute("aria-label")).toContain("corrupt");
	});
});

describe("OfflinePackPreparingBanner, data-pack-status", () => {
	const statuses: OfflinePackStatus[] = [
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
			render(<OfflinePackPreparingBanner visible={true} status={status} />);
			const region = screen.getByRole("status");
			expect(region.getAttribute("data-pack-status")).toBe(status);
		});
	}

	it("does NOT render the data-pack-status attribute when invisible", () => {
		render(<OfflinePackPreparingBanner visible={false} status="downloading" />);
		expect(document.querySelector("[data-pack-status]")).toBeNull();
	});
});
