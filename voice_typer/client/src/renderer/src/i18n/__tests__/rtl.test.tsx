/**
 * I18N-3: Render test for RTL document direction.
 *
 * Validates that {@link setLocale} updates `document.documentElement.dir` to
 * `"rtl"` for right-to-left locales (currently only Arabic) and `"ltr"` for
 * all other locales.  The test mounts a minimal React component so it exercises
 * the same render path a real component would follow.
 */
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { getLocale, isRtlLocale, type Locale, setLocale } from "@/i18n/i18n";

/**
 * Minimal component that reads the current locale via `isRtlLocale` and
 * renders the direction as text so we can assert it in the DOM.
 */
function DirectionDisplay() {
	return (
		<div data-testid="dir-display">
			{isRtlLocale(getLocale()) ? "rtl" : "ltr"}
		</div>
	);
}

describe("I18N-3: RTL document direction", () => {
	beforeEach(() => {
		// Reset to a known baseline — English, which is LTR.
		act(() => {
			setLocale("en" as Locale);
		});
		// Clear the dir and lang attributes so each test starts clean.
		document.documentElement.removeAttribute("dir");
		document.documentElement.removeAttribute("lang");
	});

	afterEach(() => {
		act(() => {
			setLocale("en" as Locale);
		});
		cleanup();
	});

	it("sets dir=rtl and lang=ar on document.documentElement when locale is Arabic", () => {
		act(() => {
			setLocale("ar" as Locale);
		});
		expect(document.documentElement.dir).toBe("rtl");
		expect(document.documentElement.lang).toBe("ar");
	});

	it("sets dir=ltr and lang=<locale> on document.documentElement for non-RTL locales", () => {
		const ltrLocales: Locale[] = ["en", "es", "fr", "de", "ru", "zh", "hi"];
		for (const loc of ltrLocales) {
			act(() => {
				setLocale(loc);
			});
			expect(document.documentElement.dir).toBe("ltr");
			expect(document.documentElement.lang).toBe(loc);
		}
	});

	it("isRtlLocale returns false for non-RTL locales and true for ar", () => {
		expect(isRtlLocale("en" as Locale)).toBe(false);
		expect(isRtlLocale("fr" as Locale)).toBe(false);
		expect(isRtlLocale("zh" as Locale)).toBe(false);
		expect(isRtlLocale("ar" as Locale)).toBe(true);
	});

	it("flips dir back to ltr and lang back to en when switching from Arabic to English", () => {
		// Switch to Arabic first.
		act(() => {
			setLocale("ar" as Locale);
		});
		expect(document.documentElement.dir).toBe("rtl");
		expect(document.documentElement.lang).toBe("ar");

		// Switch back to English.
		act(() => {
			setLocale("en" as Locale);
		});
		expect(document.documentElement.dir).toBe("ltr");
		expect(document.documentElement.lang).toBe("en");
	});

	it("renders a mounted component with the correct dir and lang after setLocale", () => {
		// Mount while in Arabic.
		act(() => {
			setLocale("ar" as Locale);
		});
		render(<DirectionDisplay />);
		// The assertion: document.documentElement.dir is "rtl" and lang is "ar"
		// for Arabic.
		expect(document.documentElement.dir).toBe("rtl");
		expect(document.documentElement.lang).toBe("ar");
	});
});
