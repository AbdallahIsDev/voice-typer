/**
 * Tests for `components/common/ExternalLink.tsx` (MO-118).
 *
 * Pins the contract every external help / feedback / changelog link
 * depends on: the element stays a REAL anchor (role=link + href, so
 * copy-link / middle-click / the existing page tests keep working), but
 * activating it is routed through the shared host opener instead of
 * navigating the webview itself.
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ExternalLink } from "@/components/common/ExternalLink";

const URL =
	"https://github.com/AbdallahIsDev/voice-typer/blob/main/SECURITY.md";

afterEach(() => {
	vi.unstubAllGlobals();
	// The namespace is optional on the bridge type, so `delete` is legal.
	delete window.window_;
});

describe("ExternalLink", () => {
	it("renders a real anchor with the external-link rel/target", () => {
		render(<ExternalLink href={URL}>Security</ExternalLink>);

		const link = screen.getByRole("link", { name: "Security" });
		expect(link).toHaveAttribute("href", URL);
		expect(link).toHaveAttribute("target", "_blank");
		expect(link).toHaveAttribute("rel", "noreferrer noopener");
	});

	it("routes the activation through the host opener and keeps the webview put", () => {
		const openExternalUrl = vi.fn().mockResolvedValue({ success: true });
		// @ts-expect-error partial bridge for the test
		window.window_ = { openExternalUrl };
		const windowOpen = vi.fn();
		vi.stubGlobal("open", windowOpen);

		render(<ExternalLink href={URL}>Security</ExternalLink>);
		const link = screen.getByRole("link", { name: "Security" });
		// `fireEvent.click` returns false when the default was prevented.
		const notPrevented = fireEvent.click(link);

		expect(openExternalUrl).toHaveBeenCalledWith(URL);
		expect(notPrevented).toBe(false);
		// The anchor's own navigation must NOT fire (that is what would
		// trap the page inside the Tauri webview).
		expect(windowOpen).not.toHaveBeenCalled();
	});

	it("lets a caller's own onClick run, and respects preventDefault", () => {
		const openExternalUrl = vi.fn();
		// @ts-expect-error partial bridge for the test
		window.window_ = { openExternalUrl };
		const onClick = vi.fn((event: React.MouseEvent) => event.preventDefault());

		render(
			<ExternalLink href={URL} onClick={onClick}>
				Security
			</ExternalLink>,
		);
		fireEvent.click(screen.getByRole("link", { name: "Security" }));

		expect(onClick).toHaveBeenCalledTimes(1);
		expect(openExternalUrl).not.toHaveBeenCalled();
	});

	it("falls back to the classic window.open when the bridge is absent", () => {
		const windowOpen = vi.fn().mockReturnValue({});
		vi.stubGlobal("open", windowOpen);

		render(<ExternalLink href={URL}>Security</ExternalLink>);
		fireEvent.click(screen.getByRole("link", { name: "Security" }));

		expect(windowOpen).toHaveBeenCalledWith(
			URL,
			"_blank",
			"noopener,noreferrer",
		);
	});

	it("forwards the rest props (className from a `Button asChild` slot)", () => {
		render(
			<ExternalLink href={URL} className="merged-class">
				Security
			</ExternalLink>,
		);
		expect(screen.getByRole("link", { name: "Security" })).toHaveClass(
			"merged-class",
		);
	});
});
