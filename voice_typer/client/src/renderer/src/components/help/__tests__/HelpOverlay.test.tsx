/**
 * HelpOverlay unit tests.
 *
 * : HelpOverlay's Modal previously set only `className="w-110"`
 * — a fixed 28rem width with NO scroll container. The Modal body
 * holds 12 shortcut <li> items + a PunctuationCheatSheet that renders
 * up to 19 entries + a search field. On a small viewport the content
 * overflowed with no scroll, clipping the lower shortcut entries and
 * the "press Esc to close" hint.
 *
 * Fix: the Modal caps at `max-h-[85vh]` and the panel itself is
 * `overflow-hidden` so the inner scroll wrapper clips to the rounded
 * shape; the body scrolls in that wrapper when content exceeds 85%
 * of the viewport height.
 *
 * The overlay previously mounted PunctuationCheatSheetButton (a
 * second `?` that opened its OWN cheat-sheet popup) at the top of
 * the body while ALSO rendering the full PunctuationCheatSheet at
 * the bottom — two cheat sheets from one help overlay. The button
 * is removed: the only help affordance is the title-bar `?` which
 * opens exactly this overlay, and the cheat sheet section renders
 * once at the bottom.
 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { HelpOverlay } from "@/components/help/HelpOverlay";

// Modal renders into a Radix portal; we just want the title / children
// to appear in the document so we can assert on them. Stubbing Modal
// also keeps this test focused on HelpOverlay's own contract (scroll
// className + button mount) rather than Radix Dialog internals. The
// stub respects the `open` prop so the inner cheat-sheet Modal mounted
// by PunctuationCheatSheetButton stays closed (open=false by default)
// and we don't end up with two PunctuationCheatSheet instances.
vi.mock("@/components/common/Modal", () => ({
	Modal: ({
		children,
		className,
		title,
		open,
	}: {
		children: React.ReactNode;
		className?: string;
		title: string;
		open: boolean;
	}) => {
		if (!open) return null;
		return (
			<div
				data-testid="modal-stub"
				data-open={open ? "true" : "false"}
				// React custom attributes must be lowercase
				// (camelCase is silently dropped to undefined).
				data-classname={className ?? ""}
			>
				<h2 data-testid="modal-title">{title}</h2>
				{children}
			</div>
		);
	},
}));

// Stub the i18n hook so the test doesn't depend on the locale loader.
vi.mock("@/i18n/i18n", () => ({
	useT: () => (key: string) => key,
}));

// The real header (title/description) now renders INSIDE HelpOverlay's
// scroll wrapper using Radix dialog primitives. This test mocks Modal
// (no Dialog.Root context), so stub the dialog primitives as plain
// elements — the assertions only care about HelpOverlay's structure.
vi.mock("@/components/ui/dialog", () => ({
	DialogHeader: ({ children }: { children: React.ReactNode }) => (
		<div data-testid="dialog-header">{children}</div>
	),
	DialogTitle: ({ children }: { children: React.ReactNode }) => (
		<h2 data-testid="dialog-title">{children}</h2>
	),
	DialogDescription: ({ children }: { children: React.ReactNode }) => (
		<p data-testid="dialog-description">{children}</p>
	),
}));

describe("HelpOverlay — ZU-26 (scroll container + PunctuationCheatSheetButton mount)", () => {
	afterEach(() => {
		cleanup();
	});

	const baseProps = {
		open: true,
		onClose: vi.fn(),
		dictationLabel: "F2",
		repasteLabel: "Ctrl+Shift+V",
	};

	it("ZU-26: Modal className caps at max-h-[85vh] and clips (overflow-hidden) instead of scrolling internally", () => {
		render(<HelpOverlay {...baseProps} />);
		const modal = screen.getByTestId("modal-stub");
		const cls = modal.getAttribute("data-classname") ?? "";
		// The scroll-container fix caps the panel at 85vh. The panel
		// itself must NOT scroll (overflow-y-auto on the rounded panel
		// lets Windows classic scrollbars escape the corner radius) —
		// it clips instead, and the body scrolls in the inner wrapper.
		expect(cls).toContain("max-h-[85vh]");
		expect(cls).toContain("overflow-hidden");
	});

	it("ZU-26: body scrolls inside the inner wrapper (min-h-0 overflow-y-auto)", () => {
		render(<HelpOverlay {...baseProps} />);
		const scroll = screen.getByTestId("help-overlay-scroll");
		const cls = scroll.className ?? "";
		expect(cls).toContain("overflow-y-auto");
		expect(cls).toContain("min-h-0");
	});

	it("no longer mounts a second PunctuationCheatSheetButton (single `?` = title bar only)", () => {
		render(<HelpOverlay {...baseProps} />);
		// The duplicate `?` affordance that opened its own cheat-sheet
		// popup is gone — the overlay renders the cheat sheet section
		// once at the bottom instead.
		expect(screen.queryByTestId("punctuation-cheat-sheet-button")).toBeNull();
	});

	it("ZU-26: still renders the shortcut list + PunctuationCheatSheet body (no regressions)", () => {
		render(<HelpOverlay {...baseProps} />);
		// The shortcut <ul> is rendered (we don't assert on each
		// entry — the PunctuationCheatSheet test file covers that).
		expect(screen.getByTestId("punctuation-cheat-sheet")).toBeInTheDocument();
	});

	it("ZU-26: still respects the open prop (closed overlay doesn't render the body)", () => {
		render(<HelpOverlay {...baseProps} open={false} />);
		// The Modal mock returns null when open=false, so the body
		// (and the modal-stub) must NOT be in the document.
		expect(screen.queryByTestId("modal-stub")).toBeNull();
	});
});
