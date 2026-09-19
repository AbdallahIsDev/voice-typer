import { act, cleanup, render } from "@testing-library/react";
import type { ToasterProps } from "sonner";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Capture the most recent props passed to the mocked Toaster so each
// test can assert on the `position` value after a locale change.
let lastToasterProps: ToasterProps | null = null;

vi.mock("sonner", () => ({
	Toaster: (props: ToasterProps) => {
		lastToasterProps = props;
		return <div data-testid="mocked-toaster" />;
	},
}));

// Import AFTER the mock so the mock is wired up before module eval.
// `setLocale` is imported lazily inside each test's `act` so we can
// reset to a known baseline without polluting other test files.
import { type Locale, setLocale } from "@/i18n/i18n";
import { Toaster } from "../sonner";

afterEach(() => {
	cleanup();
	// Always restore to English so subsequent test files start clean.
	act(() => {
		setLocale("en" as Locale);
	});
});

beforeEach(() => {
	lastToasterProps = null;
	act(() => {
		setLocale("en" as Locale);
	});
});

describe("Sonner Toaster, BG-38 reactive position", () => {
	it("uses bottom-right position when the locale is LTR (English)", () => {
		render(<Toaster />);
		expect(lastToasterProps).not.toBeNull();
		expect(lastToasterProps?.position).toBe("bottom-right");
	});

	it("uses bottom-left position when the locale is RTL (Arabic)", () => {
		act(() => {
			setLocale("ar" as Locale);
		});
		render(<Toaster />);
		expect(lastToasterProps).not.toBeNull();
		expect(lastToasterProps?.position).toBe("bottom-left");
	});

	it("re-renders with the new position when the locale changes at runtime (no full reload)", () => {
		// Mount while English, initial position is bottom-right.
		const { rerender } = render(<Toaster />);
		expect(lastToasterProps?.position).toBe("bottom-right");

		// Switch to Arabic at runtime, the component must re-render
		// (via useSyncExternalStore) and pass the new position.
		act(() => {
			setLocale("ar" as Locale);
		});
		// `rerender` ensures React flushes the state update synchronously
		// even if the subscription notification is async.
		rerender(<Toaster />);
		expect(lastToasterProps?.position).toBe("bottom-left");

		// Switch back to English, position flips back to bottom-right.
		act(() => {
			setLocale("en" as Locale);
		});
		rerender(<Toaster />);
		expect(lastToasterProps?.position).toBe("bottom-right");
	});
});

describe("Sonner Toaster, ZU-33 stacking configuration", () => {
	it("sets visibleToasts=6 so a flap or burst keeps recent history visible", () => {
		// sonner's default is visibleToasts=3, older toasts in the queue
		// are hidden until newer ones expire. During a backend flap or a
		// burst of error toasts (save + export + download all failing),
		// the user only saw the 3 newest and lost the context of what
		// else had failed. 6 keeps the recent history visible without
		// flooding the corner.
		render(<Toaster />);
		expect(lastToasterProps).not.toBeNull();
		expect(lastToasterProps?.visibleToasts).toBe(6);
	});

	it("sets expand=false so the stack stays collapsed by default", () => {
		// Sonner expands the stack on hover (or when ``expand`` is true)
		//, collapsed shows only the newest toast prominently with older
		// ones peeking. Collapsed-by-default keeps the corner uncluttered;
		// the user can hover to read the queue.
		render(<Toaster />);
		expect(lastToasterProps).not.toBeNull();
		expect(lastToasterProps?.expand).toBe(false);
	});

	it("uses the neutral popover surface: richColors absent, closeButton / duration intact", () => {
		// Regression guard: the new visibleToasts / expand props must not
		// clobber the existing canonical configuration. richColors is
		// intentionally gone (neutral popover surface + semantic icon
		// color via the .toaster overrides in index.css); closeButton,
		// duration, and everything below must stay pinned.
		render(<Toaster />);
		expect(lastToasterProps).not.toBeNull();
		expect(lastToasterProps).not.toHaveProperty("richColors");
		expect(lastToasterProps?.closeButton).toBe(true);
		expect(lastToasterProps?.duration).toBe(4000);
	});

	it("keeps the custom icons, popover style vars, position, and aria labels intact", () => {
		render(<Toaster />);
		expect(lastToasterProps).not.toBeNull();
		// Custom Hugeicons per type (the semantic signal in neutral mode).
		expect(Object.keys(lastToasterProps?.icons ?? {}).sort()).toEqual([
			"error",
			"info",
			"loading",
			"success",
			"warning",
		]);
		// Neutral surface mapping consumed by sonner's --normal-* vars.
		expect(lastToasterProps?.style).toMatchObject({
			"--normal-bg": "var(--popover)",
			"--normal-text": "var(--popover-foreground)",
			"--normal-border": "var(--border)",
			"--border-radius": "var(--radius)",
		});
		// Scoping class for the index.css .toaster overrides.
		expect(lastToasterProps?.className).toBe("toaster group");
		// Stacking + expansion config.
		expect(lastToasterProps?.visibleToasts).toBe(6);
		expect(lastToasterProps?.expand).toBe(false);
		// Localized accessible names (locale reset to English in
		// beforeEach; assert presence, not copy, to stay locale-proof).
		expect(typeof lastToasterProps?.containerAriaLabel).toBe("string");
		expect(typeof lastToasterProps?.toastOptions?.closeButtonAriaLabel).toBe(
			"string",
		);
	});
});
