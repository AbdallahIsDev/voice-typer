/**
 * Tests for useConnectingProgress (extracted from App.tsx).
 *
 * Contract: subscribe to the backend ``download_progress`` push event
 * and surface the percentage ONLY while the app is not yet connected —
 * updates are skipped while ``connectionStatus === "connected"`` (the
 * screen that reads the value is not rendered), and any transition
 * away from ``"connecting"`` clears the value so a stale percentage
 * can't persist across a disconnect/reconnect flap.
 */
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useConnectingProgress } from "@/hooks/useConnectingProgress";
import type { ConnectionStatus } from "@/stores/appStore";

// Capture the handler usePythonEvent registers so we can fire it.
const registered = new Map<string, (data?: unknown) => unknown>();

vi.mock("@/hooks/usePython", () => ({
	usePythonEvent: (type: string, handler: (data?: unknown) => unknown) => {
		registered.set(type, handler);
	},
}));

/** Return the download_progress handler (asserting it was registered). */
function getHandler(): (data?: unknown) => unknown {
	const handler = registered.get("download_progress");
	if (!handler) throw new Error("download_progress handler was not registered");
	return handler;
}

beforeEach(() => {
	registered.clear();
	vi.clearAllMocks();
});

afterEach(() => {
	vi.clearAllMocks();
});

describe("useConnectingProgress", () => {
	it("registers a handler for download_progress in every connection state", () => {
		// Rules-of-hooks: the subscription is always registered; the
		// gating happens inside the handler.
		const { rerender } = renderHook(
			({ status }: { status: ConnectionStatus }) =>
				useConnectingProgress(status),
			{ initialProps: { status: "connecting" } },
		);
		expect(registered.has("download_progress")).toBe(true);
		rerender({ status: "connected" as const });
		expect(registered.has("download_progress")).toBe(true);
	});

	it("updates the progress from download_progress events while connecting", () => {
		const { result } = renderHook(() => useConnectingProgress("connecting"));
		expect(result.current).toBeNull();
		act(() => {
			getHandler()({ progress: 50 });
		});
		expect(result.current).toBe(50);
	});

	it("ignores events with a non-number progress payload", () => {
		const { result } = renderHook(() => useConnectingProgress("connecting"));
		act(() => {
			getHandler()({ progress: "50" });
			getHandler()({});
			getHandler()(undefined);
		});
		expect(result.current).toBeNull();
	});

	it("skips the update while connected (wasted re-render guard)", () => {
		const { result, rerender } = renderHook(
			({ status }: { status: ConnectionStatus }) =>
				useConnectingProgress(status),
			{ initialProps: { status: "connecting" } },
		);
		act(() => {
			getHandler()({ progress: 42 });
		});
		expect(result.current).toBe(42);

		rerender({ status: "connected" as const });
		act(() => {
			getHandler()({ progress: 99 });
		});
		// The 99 event was a no-op; the leave-connecting effect ALSO
		// cleared the stale 42 so a reconnect doesn't resume showing it.
		expect(result.current).toBeNull();
	});

	it("clears the progress on any transition away from connecting", () => {
		const { result, rerender } = renderHook(
			({ status }: { status: ConnectionStatus }) =>
				useConnectingProgress(status),
			{ initialProps: { status: "connecting" } },
		);
		act(() => {
			getHandler()({ progress: 73 });
		});
		expect(result.current).toBe(73);
		rerender({ status: "restarting" });
		expect(result.current).toBeNull();
	});
});
