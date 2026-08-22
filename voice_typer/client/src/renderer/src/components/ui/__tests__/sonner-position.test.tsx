/**
 * Toaster position must react to runtime locale changes.
 *
 * Previously the position was computed once at mount from
 * `isRtlLocale(getLocale())` — switching to Arabic at runtime kept the
 * toaster pinned bottom-right until a page reload. The fix subscribes
 * via the i18n module's locale-subscriber registry (useSyncExternalStore),
 * so the position mirrors the ACTIVE locale: bottom-right in LTR,
 * bottom-left in RTL.
 *
 * The `sonner` module is mocked to capture the props the wrapper passes
 * through, letting us assert the position across a simulated locale flip
 * without rendering sonner's portal tree.
 */
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Mutable locale flag + subscriber registry mirroring the i18n store's
// notification stream (setLocale → notifyLocaleSubscribers).
let rtlLocale = false;
const localeListeners = new Set<() => void>();

vi.mock("@/i18n/i18n", () => ({
	getLocale: () => (rtlLocale ? "ar" : "en"),
	isRtlLocale: (locale: string) => locale === "ar",
	subscribeLocale: (cb: () => void) => {
		localeListeners.add(cb);
		return () => {
			localeListeners.delete(cb);
		};
	},
}));

vi.mock("sonner", () => ({
	Toaster: (props: unknown) => {
		capturedProps.push(props as { position?: string });
		return null;
	},
}));

const capturedProps: Array<{ position?: string }> = [];

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

import { Toaster } from "@/components/ui/sonner";

function lastPosition(): string | undefined {
	return capturedProps[capturedProps.length - 1]?.position;
}

describe("Toaster — locale-reactive position", () => {
	beforeEach(() => {
		cleanup();
		capturedProps.length = 0;
		rtlLocale = false;
		localeListeners.clear();
	});

	afterEach(() => {
		cleanup();
	});

	it("renders bottom-right for LTR locales at mount", () => {
		render(<Toaster />);
		expect(lastPosition()).toBe("bottom-right");
	});

	it("flips to bottom-left when the locale switches to an RTL locale at runtime", () => {
		render(<Toaster />);
		expect(lastPosition()).toBe("bottom-right");

		// Simulate setLocale("ar"): mutate the resolved locale, then fire
		// the same subscriber notification the i18n store dispatches.
		rtlLocale = true;
		act(() => {
			for (const cb of [...localeListeners]) cb();
		});

		expect(localeListeners.size).toBeGreaterThan(0);
		expect(lastPosition()).toBe("bottom-left");
	});

	it("flips back to bottom-right when the locale returns to LTR", () => {
		render(<Toaster />);
		rtlLocale = true;
		act(() => {
			for (const cb of [...localeListeners]) cb();
		});
		expect(lastPosition()).toBe("bottom-left");

		rtlLocale = false;
		act(() => {
			for (const cb of [...localeListeners]) cb();
		});
		expect(lastPosition()).toBe("bottom-right");
	});
});
