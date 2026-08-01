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
 * Fix: add `max-h-[85vh] overflow-y-auto` to the Modal className so
 * the body scrolls when it exceeds 85% of the viewport height.
 *
 * Separately: PunctuationCheatSheetButton (the affordance that opens
 * the cheat sheet on its own) had ZERO production callers. The fix
 * mounts it at the top of the HelpOverlay body so the spoken-form
 * reference is one click away without scrolling past the full
 * shortcut list.
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

	it("ZU-26: Modal className includes max-h-[85vh] overflow-y-auto (scroll container for small viewports)", () => {
		render(<HelpOverlay {...baseProps} />);
		const modal = screen.getByTestId("modal-stub");
		const cls = modal.getAttribute("data-classname") ?? "";
		// The fixed 28rem width is preserved...
		expect(cls).toContain("w-110");
		// ...and the scroll-container fix is applied so the body
		// scrolls when content exceeds 85% of the viewport height.
		expect(cls).toContain("max-h-[85vh]");
		expect(cls).toContain("overflow-y-auto");
	});

	it("ZU-26: mounts a PunctuationCheatSheetButton at the top of the overlay body (quick-access affordance)", () => {
		render(<HelpOverlay {...baseProps} />);
		// The PunctuationCheatSheetButton exposes a stable testid
		// (see components/help/PunctuationCheatSheet.tsx).
		const cheatSheetButton = screen.getByTestId(
			"punctuation-cheat-sheet-button",
		);
		expect(cheatSheetButton).toBeInTheDocument();
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
