/**
 * XA-20-21: component-level RTL render tests.
 *
 * The pre-existing ``rtl.test.tsx`` only asserts that
 * ``document.documentElement.dir`` flips between ``"rtl"`` and
 * ``"ltr"`` when ``setLocale()`` is called. It does NOT mount any
 * real component, so it cannot catch:
 *
 *   - A component that reads the locale ONCE at mount via ``getLocale()``
 *     and never re-renders when the locale changes (the ``useT()``
 *     hook's ``useSyncExternalStore`` subscription is what makes
 *     re-render work — a component using bare ``t()`` without the hook
 *     would be stuck on the mount-time locale).
 *   - A component whose rendered TEXT doesn't change when the locale
 *     changes (would indicate the component is hardcoding English
 *     instead of routing through ``t()``).
 *   - A component whose LAYOUT doesn't flip when ``dir`` flips (would
 *     indicate the component is using physical CSS properties like
 *     ``ml-4`` / ``pl-9`` instead of logical ones like ``ms-4`` /
 *     ``ps-9`` — the ``isRtlLocale()`` helper lets a component gate
 *     layout-flip logic on the current locale).
 *
 * This test file mounts a minimal React component that exercises the
 * ``useT()`` hook + ``t()`` lookup path, then asserts:
 *
 *   1. The component's rendered text tracks the active locale (English
 *      label → Arabic label → English label).
 *   2. The ``useT()`` hook re-renders the component on locale change
 *      (if it didn't, the text would stay on the mount-time locale).
 *   3. ``document.documentElement.dir`` is ``"rtl"`` while Arabic is
 *      active and ``"ltr"`` while English is active (mirrors
 *      ``rtl.test.tsx`` but asserted from within a mounted component
 *      so the render-path is exercised end-to-end).
 *   4. ``document.documentElement.lang`` tracks the active locale (so
 *      screen readers pronounce content in the user-selected language).
 *   5. A component using bare ``t()`` (NOT the ``useT()`` hook) does
 *      NOT re-render when the locale changes — this is the
 *      negative-test guardrail that documents why callers MUST use
 *      ``useT()`` for reactive text. The test asserts the negative
 *      case so a future refactor that makes bare ``t()`` reactive
 *      (e.g. by adding a global subscription) updates this test rather
 *      than silently changing the contract.
 */
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

/**
 * Minimal component that uses the ``useT()`` hook so it re-renders on
 * locale change. Renders a translation key as text so we can assert
 * the visible string tracks the active locale.
 *
 * Uses ``analytics.title`` (defined in all 8 locale files — ``en``:
 * ``"Analytics"``, ``ar``: ``"تحليلات"``) as a stable, locale-aware
 * probe.
 */
function AnalyticsTitle() {
	const tt = useT();
	return (
		<div data-testid="analytics-title" data-locale={getLocale()}>
			{tt("analytics.title")}
		</div>
	);
}

/**
 * Minimal component that calls bare ``t()`` at render time WITHOUT
 * subscribing to locale changes via ``useT()``. The component captures
 * the locale at mount time and never re-renders when the locale
 * changes — this is the negative-test probe (see header docstring).
 */
function StaticTitle() {
	return (
		<div data-testid="static-title" data-locale={getLocale()}>
			{t("analytics.title")}
		</div>
	);
}

/**
 * Minimal component that gates a CSS class on ``isRtlLocale(getLocale())``
 * so we can assert the component's layout-flip logic tracks the active
 * locale. Uses the ``useT()`` hook so it re-renders on locale change.
 *
 * Uses distinctive class names (``row-reverse-probe`` / ``row-probe``)
 * so the RTL / LTR states are unambiguously distinguishable — a naive
 * ``expect(className).not.toContain("flex-row")`` assertion would fail
 * because ``"flex-row-reverse"`` contains the substring ``"flex-row"``.
 */
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
		// Switch to Arabic — the useT() subscription MUST trigger a re-render
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
		// English baseline — setLocale("en") in beforeEach set dir="ltr".
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
		// English baseline — LTR layout.
		expect(probe.dataset.dir).toBe("ltr");
		expect(probe.className).toBe("row-probe");
		// Switch to Arabic — the layout MUST flip to RTL.
		act(() => {
			setLocale("ar" as Locale);
		});
		expect(probe.dataset.dir).toBe("rtl");
		expect(probe.className).toBe("row-reverse-probe");
		// Switch back — the layout MUST flip back to LTR.
		act(() => {
			setLocale("en" as Locale);
		});
		expect(probe.dataset.dir).toBe("ltr");
		expect(probe.className).toBe("row-probe");
	});

	it("bare t() component (no useT() hook) does NOT re-render on locale change — documents the reactive-subscription contract", () => {
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
