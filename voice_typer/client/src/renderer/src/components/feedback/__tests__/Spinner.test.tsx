/**
 * Tests for the shared Spinner component.
 *
 * : the previous implementation rendered the root element as
 * `<output aria-label="Loading">` for the non-decorative default. The
 * HTML5 `<output>` element has an implicit ARIA role of `status`, which
 * means it is treated as a polite `aria-live` region by screen readers.
 * As a result, AT users heard "Loading" announced every time ANY page
 * rendered a Spinner (e.g. while data was being fetched on the History,
 * Vocabulary, Templates, Microphone, Models, Settings, and Onboarding
 * pages) — even though the spinner in those contexts is incidental,
 * not a primary status message.
 *
 * The fix (): the default root is now a `<span role="img">`
 * with `aria-label={t("a11y.loading")}`. It carries the accessible
 * name (so AT users hear "Loading" when they focus the spinner) but
 * does NOT have an implicit live region. Pages that want a status
 * announcement (e.g. ConnectionStatusScreen while the backend is
 * starting) wrap the Spinner in their own `<output aria-live="polite">`.
 *
 * These tests pin both contracts:
 *   1. Default root is `<span role="img">` with no implicit aria-live.
 *   2. `decorative` prop renders `<div aria-hidden>` (unchanged).
 *   3. `aria-label` is wired through `t("a11y.loading")`.
 *   4. The size + className merge behaviour is preserved.
 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Spinner } from "../Spinner";

// Mock i18n so we don't load the real locale chunks in unit tests.
// The mock returns the key as the translated string so tests can
// assert on a stable value.
vi.mock("@/i18n/i18n", () => ({
	t: (key: string) => key,
	useT: () => (key: string) => key,
}));

afterEach(() => {
	cleanup();
});

describe("Spinner — S5-CR-100 (no implicit aria-live region)", () => {
	beforeEach(() => {
		// jsdom doesn't ship a layout engine, so the cn() merge
		// is just string concatenation. We don't need any DOM
		// globals beyond what testing-library sets up.
	});

	it("default render is a <span role=img> with aria-label (NOT an <output> live region)", () => {
		const { container } = render(<Spinner />);
		// The root element must NOT be <output> — that would
		// re-introduce the implicit aria-live="polite" region
		//removed.
		expect(container.querySelector("output")).toBeNull();
		// The accessible name flows from aria-label (the i18n mock
		// returns the key, so the label is the literal key string).
		const img = screen.getByRole("img", { name: "a11y.loading" });
		expect(img.tagName).toBe("SPAN");
	});

	it("default render does NOT carry an implicit or explicit aria-live attribute", () => {
		const { container } = render(<Spinner />);
		const root = container.firstElementChild;
		expect(root).not.toBeNull();
		expect(root?.getAttribute("aria-live")).toBeNull();
		// The implicit role of <output> would have surfaced as
		// role="status" via jsdom's ARIA mapping; assert we are
		// explicitly NOT a status region.
		expect(root?.getAttribute("role")).toBe("img");
	});

	it("default render carries the loading aria-label from the i18n catalog", () => {
		render(<Spinner />);
		// The mock translator returns the key, so the label is
		// the literal key string.
		expect(
			screen.getByRole("img", { name: "a11y.loading" }),
		).toBeInTheDocument();
	});

	it("decorative prop renders a plain <div aria-hidden=true> with no role and no aria-label", () => {
		const { container } = render(<Spinner decorative />);
		const root = container.firstElementChild;
		expect(root).not.toBeNull();
		expect(root?.tagName).toBe("DIV");
		expect(root?.getAttribute("aria-hidden")).toBe("true");
		expect(root?.getAttribute("role")).toBeNull();
		expect(root?.getAttribute("aria-label")).toBeNull();
	});

	it("applies the size via inline style (PVT-025 — Tailwind JIT can't see dynamic class names)", () => {
		const { container } = render(<Spinner size={24} />);
		const root = container.firstElementChild as HTMLElement;
		expect(root.style.width).toBe("24px");
		expect(root.style.height).toBe("24px");
	});

	it("default size is 16px when no size prop is passed", () => {
		const { container } = render(<Spinner />);
		const root = container.firstElementChild as HTMLElement;
		expect(root.style.width).toBe("16px");
		expect(root.style.height).toBe("16px");
	});

	it("applies the base animate-spin classes", () => {
		const { container } = render(<Spinner />);
		const root = container.firstElementChild as HTMLElement;
		expect(root.className).toContain("animate-spin");
		expect(root.className).toContain("rounded-full");
		expect(root.className).toContain("border-accent");
		expect(root.className).toContain("border-t-transparent");
	});

	it("merges consumer className (tailwind-merge) — border-current overrides border-accent", () => {
		const { container } = render(
			<Spinner className="border-current h-3 w-3" />,
		);
		const root = container.firstElementChild as HTMLElement;
		// tailwind-merge should drop `border-accent` in favour of
		// the consumer-supplied `border-current`.
		expect(root.className).toContain("border-current");
		expect(root.className).not.toContain("border-accent");
	});

	it("decorative prop preserves the size + className merge behaviour", () => {
		const { container } = render(
			<Spinner decorative size={20} className="border-current" />,
		);
		const root = container.firstElementChild as HTMLElement;
		expect(root.tagName).toBe("DIV");
		expect(root.style.width).toBe("20px");
		expect(root.style.height).toBe("20px");
		expect(root.className).toContain("border-current");
		expect(root.className).toContain("animate-spin");
	});
});
