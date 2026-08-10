/**
 * Tests for usePasteFailedToast (extracted from App.tsx, EO-28).
 *
 * Contract: subscribe to the backend ``paste_failed`` push event and
 * surface a sonner warning. With a ``recovery_path`` the toast carries
 * a "Copy path" action that writes the path to the clipboard.
 */
import { renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { toast } from "sonner";

import { usePasteFailedToast } from "@/hooks/usePasteFailedToast";

// Capture the handler usePythonEvent registers so we can fire it.
const registered = new Map<string, (data?: unknown) => unknown>();
const mockT = vi.fn((key: string) => key);

vi.mock("@/hooks/usePython", () => ({
	usePythonEvent: (type: string, handler: (data?: unknown) => unknown) => {
		registered.set(type, handler);
	},
}));

vi.mock("sonner", () => ({
	toast: { warning: vi.fn() },
}));

const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});

beforeEach(() => {
	registered.clear();
	vi.clearAllMocks();
});

afterEach(() => {
	warnSpy.mockClear();
});

describe("usePasteFailedToast", () => {
	it("registers a handler for paste_failed", () => {
		renderHook(() => usePasteFailedToast(mockT));
		expect(registered.has("paste_failed")).toBe(true);
	});

	it("shows a warning toast with default message when payload has none", () => {
		renderHook(() => usePasteFailedToast(mockT));
		const handler = registered.get("paste_failed")!;
		handler({});
		expect(toast.warning).toHaveBeenCalledWith("home.pasteFailedMessage", {
			description: undefined,
			duration: 8000,
		});
	});

	it("uses the message from the payload (multi-line → title + description)", () => {
		renderHook(() => usePasteFailedToast(mockT));
		const handler = registered.get("paste_failed")!;
		handler({ message: "Paste failed\nclipboard is busy" });
		expect(toast.warning).toHaveBeenCalledWith("Paste failed", {
			description: "clipboard is busy",
			duration: 8000,
		});
	});

	it("adds a Copy-path action that writes the recovery path", () => {
		const writeText = vi.fn().mockResolvedValue(undefined);
		Object.assign(navigator, { clipboard: { writeText } });
		renderHook(() => usePasteFailedToast(mockT));
		const handler = registered.get("paste_failed")!;
		handler({ message: "Paste failed", recovery_path: "/tmp/recovered.txt" });
		expect(toast.warning).toHaveBeenCalledWith(
			"Paste failed",
			expect.objectContaining({ action: expect.any(Object) }),
		);
		const call = (toast.warning as ReturnType<typeof vi.fn>).mock.calls[0];
		const action = call?.[1]?.action as { onClick: () => void } | undefined;
		expect(action).toBeDefined();
		action?.onClick();
		expect(writeText).toHaveBeenCalledWith("/tmp/recovered.txt");
	});

	it("does not add an action without a recovery path", () => {
		renderHook(() => usePasteFailedToast(mockT));
		const handler = registered.get("paste_failed")!;
		handler({ message: "Paste failed" });
		const call = (toast.warning as ReturnType<typeof vi.fn>).mock.calls[0];
		expect(call?.[1]).not.toHaveProperty("action");
	});
});
