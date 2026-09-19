import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
	ensureLocaleLoaded,
	getLocale,
	isRtlLocale,
	type Locale,
	setLocale,
	t,
	useT,
} from "@/i18n/i18n";

function AnalyticsTitle() {
	const tt = useT();
	return (
		<div data-testid="analytics-title" data-locale={getLocale()}>
			{tt("analytics.title")}
		</div>
	);
}

function StaticTitle() {
	return (
		<div data-testid="static-title" data-locale={getLocale()}>
			{t("analytics.title")}
		</div>
	);
}

function LayoutProbe() {
	useT();
	const rtl = isRtlLocale(getLocale());
	return (
		<div
			data-testid="layout-probe"
			data-dir={rtl ? "rtl" : "ltr"}
			className={rtl ? "row-reverse-probe" : "row-probe"}
		/>
	);
}

describe("XA-20-21: component-level RTL render behavior", () => {
	beforeEach(async () => {
		// Reset to a known baseline (English, LTR). setLocale also sets
		// document.documentElement.dir / lang, so we DON'T clear those
		// attributes afterwards (clearing them would leave dir="" which
		// is neither "ltr" nor "rtl" and breaks the baseline assertions).
		await act(async () => {
			setLocale("en" as Locale);
			// Pre-load the Arabic translations so the first setLocale("ar")
			// in a test immediately has the Arabic strings available
			// (without this, t("analytics.title") would fall back to
			// English until the dynamic import resolved).
			await ensureLocaleLoaded("ar" as Locale);
		});
	});

	afterEach(() => {
		act(() => {
			setLocale("en" as Locale);
		});
		cleanup();
	});

	it("useT()-subscribed component re-renders with Arabic text when locale switches to ar", () => {
		render(<AnalyticsTitle />);
		// English baseline.
		expect(screen.getByTestId("analytics-title").textContent).toBe("Analytics");
		// Switch to Arabic, the useT() subscription MUST trigger a re-render
		// with the Arabic translation. If the component used bare t() without
		// useT(), the text would stay "Analytics" (the mount-time locale).
		act(() => {
			setLocale("ar" as Locale);
		});
		expect(screen.getByTestId("analytics-title").textContent).toBe("تحليلات");
		expect(screen.getByTestId("analytics-title").dataset.locale).toBe("ar");
	});

	it("useT()-subscribed component flips back to English text when locale switches back from ar", () => {
		render(<AnalyticsTitle />);
		act(() => {
			setLocale("ar" as Locale);
		});
		expect(screen.getByTestId("analytics-title").textContent).toBe("تحليلات");
		act(() => {
			setLocale("en" as Locale);
		});
		expect(screen.getByTestId("analytics-title").textContent).toBe("Analytics");
	});

	it("document.documentElement.dir + lang track the active locale while a component is mounted", () => {
		render(<AnalyticsTitle />);
		// English baseline, setLocale("en") in beforeEach set dir="ltr".
		expect(document.documentElement.dir).toBe("ltr");
		expect(document.documentElement.lang).toBe("en");
		act(() => {
			setLocale("ar" as Locale);
		});
		expect(document.documentElement.dir).toBe("rtl");
		expect(document.documentElement.lang).toBe("ar");
		act(() => {
			setLocale("fr" as Locale);
		});
		expect(document.documentElement.dir).toBe("ltr");
		expect(document.documentElement.lang).toBe("fr");
	});

	it("LayoutProbe component flips its CSS class + data-dir attribute when locale switches to/from ar", () => {
		render(<LayoutProbe />);
		const probe = screen.getByTestId("layout-probe");
		// English baseline, LTR layout.
		expect(probe.dataset.dir).toBe("ltr");
		expect(probe.className).toBe("row-probe");
		// Switch to Arabic, the layout MUST flip to RTL.
		act(() => {
			setLocale("ar" as Locale);
		});
		expect(probe.dataset.dir).toBe("rtl");
		expect(probe.className).toBe("row-reverse-probe");
		// Switch back, the layout MUST flip back to LTR.
		act(() => {
			setLocale("en" as Locale);
		});
		expect(probe.dataset.dir).toBe("ltr");
		expect(probe.className).toBe("row-probe");
	});

	it("bare t() component (no useT() hook) does NOT re-render on locale change, documents the reactive-subscription contract", () => {
		// This is a NEGATIVE test: it asserts that bare t() (without the
		// useT() hook) is NOT reactive. If a future refactor makes bare
		// t() reactive (e.g. by adding a global subscription), this test
		// will fail and MUST be updated to assert the new reactive
		// behaviour. The test exists so the contract change is explicit.
		render(<StaticTitle />);
		expect(screen.getByTestId("static-title").textContent).toBe("Analytics");
		act(() => {
			setLocale("ar" as Locale);
		});
		// The component did NOT subscribe via useT(), so it does not
		// re-render. Its text stays on the mount-time locale (English).
		expect(screen.getByTestId("static-title").textContent).toBe("Analytics");
		// The dataset.locale attribute ALSO stays at the mount-time value
		// (the component didn't re-render, so the attribute wasn't updated).
		expect(screen.getByTestId("static-title").dataset.locale).toBe("en");
	});

	it("useT()-subscribed component survives a rapid locale-flip cycle (en → ar → en → ar) without drift", () => {
		// Stress test: rapid locale flipping can race the
		// useSyncExternalStore subscription if the subscriber set is
		// mutated mid-iteration. Assert the final state matches the
		// final locale (no stale text from an intermediate locale).
		render(<AnalyticsTitle />);
		act(() => {
			setLocale("ar" as Locale);
		});
		act(() => {
			setLocale("en" as Locale);
		});
		act(() => {
			setLocale("ar" as Locale);
		});
		expect(screen.getByTestId("analytics-title").textContent).toBe("تحليلات");
		expect(document.documentElement.dir).toBe("rtl");
		expect(document.documentElement.lang).toBe("ar");
	});
});
