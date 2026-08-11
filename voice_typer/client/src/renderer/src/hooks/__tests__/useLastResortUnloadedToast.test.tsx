/**
 * Tests for useLastResortUnloadedToast.
 *
 * Contract: subscribe to the backend ``asr_last_resort_unloaded`` push
 * event and surface a sonner warning pointing at the Models page, with an
 * "Open Models" action that calls the injected navigation callback. The
 * toast is rate-limited in TWO layers: a per-backend 15-min cooldown
 * (mirroring the server's ModelManager ``_LAST_RESORT_NOTIFY_COOLDOWN_SECS``)
 * so a permanently-unloaded backend re-notifies at most ~4x/hour, plus a
 * short GLOBAL dedupe window (``LAST_RESORT_TOAST_DEDUPE_MS`` = 10s) so
 * rapid genuine transitions across DIFFERENT backends collapse to one
 * visible notification instead of stacking a toast per backend.
 */
import { renderHook } from "@testing-library/react";
import { toast } from "sonner";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
	_resetLastResortToastCooldownForTest,
	useLastResortUnloadedToast,
} from "@/hooks/useLastResortUnloadedToast";

// Capture the handler usePythonEvent registers so we can fire it.
const registered = new Map<string, (data?: unknown) => unknown>();
const mockT = vi.fn((key: string, params?: Record<string, string>) =>
	params?.backend ? `${key}(${params.backend})` : key,
);
const onOpenModels = vi.fn();

vi.mock("@/hooks/usePython", () => ({
	usePythonEvent: (type: string, handler: (data?: unknown) => unknown) => {
		registered.set(type, handler);
	},
}));

vi.mock("sonner", () => ({
	toast: { warning: vi.fn() },
}));

let nowSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
	registered.clear();
	vi.clearAllMocks();
	_resetLastResortToastCooldownForTest();
	nowSpy = vi.spyOn(Date, "now").mockReturnValue(1_000_000);
});

afterEach(() => {
	nowSpy.mockRestore();
});

describe("useLastResortUnloadedToast", () => {
	it("registers a handler for asr_last_resort_unloaded", () => {
		renderHook(() => useLastResortUnloadedToast(mockT, onOpenModels));
		expect(registered.has("asr_last_resort_unloaded")).toBe(true);
	});

	it("shows a warning toast with the Models-page pointer and an Open Models action", () => {
		renderHook(() => useLastResortUnloadedToast(mockT, onOpenModels));
		const handler = registered.get("asr_last_resort_unloaded");
		expect(handler).toBeDefined();
		handler?.({ backend: "whisper", timestamp: "2026-08-11T00:00:00Z" });

		expect(toast.warning).toHaveBeenCalledWith(
			"models.lastResortUnloaded(whisper)",
			{
				id: "asr-last-resort-unloaded:whisper",
				description: "models.lastResortUnloadedHint",
				duration: 8000,
				action: {
					label: "common.openModels",
					onClick: onOpenModels,
				},
			},
		);
	});

	it("falls back to 'unknown' backend when the payload has none", () => {
		renderHook(() => useLastResortUnloadedToast(mockT, onOpenModels));
		const handler = registered.get("asr_last_resort_unloaded");
		expect(handler).toBeDefined();
		handler?.({});
		expect(toast.warning).toHaveBeenCalledWith(
			"models.lastResortUnloaded(unknown)",
			expect.objectContaining({ action: expect.any(Object) }),
		);
	});

	it("suppresses repeats of the SAME backend inside the 15-min cooldown", () => {
		renderHook(() => useLastResortUnloadedToast(mockT, onOpenModels));
		const handler = registered.get("asr_last_resort_unloaded");
		expect(handler).toBeDefined();

		handler?.({ backend: "whisper" });
		handler?.({ backend: "whisper" });
		expect(toast.warning).toHaveBeenCalledTimes(1);

		// A different backend is a different transition — NOT suppressed by
		// the per-backend cooldown. But it IS collapsed by the short global
		// dedupe window if it fires within 10s of the whisper toast, so
		// advance the clock past that window first (a genuine later
		// transition, not part of the same notification burst).
		nowSpy.mockReturnValue(1_000_000 + 10_001);
		handler?.({ backend: "qwen" });
		expect(toast.warning).toHaveBeenCalledTimes(2);
	});

	it("collapses rapid genuine transitions across DIFFERENT backends to one toast", () => {
		// Renderer-side dedupe: whisper and qwen both breaking within the
		// 10s dedupe window must NOT stack two toasts — the user sees ONE
		// notification pointing at the Models page.
		renderHook(() => useLastResortUnloadedToast(mockT, onOpenModels));
		const handler = registered.get("asr_last_resort_unloaded");
		expect(handler).toBeDefined();

		handler?.({ backend: "whisper" });
		handler?.({ backend: "qwen" });
		handler?.({ backend: "parakeet" });
		expect(toast.warning).toHaveBeenCalledTimes(1);

		// The single toast still carries the first backend's label.
		expect(toast.warning).toHaveBeenCalledWith(
			"models.lastResortUnloaded(whisper)",
			expect.objectContaining({ id: "asr-last-resort-unloaded:whisper" }),
		);
	});

	it("toasts a different backend again after the dedupe window lapses", () => {
		// After the 10s dedupe window, a NEW backend breaking is a
		// genuinely separate notification cycle — not collapsed.
		renderHook(() => useLastResortUnloadedToast(mockT, onOpenModels));
		const handler = registered.get("asr_last_resort_unloaded");
		expect(handler).toBeDefined();

		handler?.({ backend: "whisper" });
		expect(toast.warning).toHaveBeenCalledTimes(1);

		nowSpy.mockReturnValue(1_000_000 + 10_001);
		handler?.({ backend: "qwen" });
		expect(toast.warning).toHaveBeenCalledTimes(2);
	});

	it("re-notifies the same backend after the cooldown lapses", () => {
		renderHook(() => useLastResortUnloadedToast(mockT, onOpenModels));
		const handler = registered.get("asr_last_resort_unloaded");
		expect(handler).toBeDefined();

		handler?.({ backend: "whisper" });
		expect(toast.warning).toHaveBeenCalledTimes(1);

		// Advance past the 15-minute cooldown (900_000 ms).
		nowSpy.mockReturnValue(1_000_000 + 900_001);
		handler?.({ backend: "whisper" });
		expect(toast.warning).toHaveBeenCalledTimes(2);
	});

	it("keeps the cooldown across hook re-mounts (state lives in the Zustand store, not the hook module)", () => {
		// First mount: toast fires, timestamp recorded in the store.
		const first = renderHook(() =>
			useLastResortUnloadedToast(mockT, onOpenModels),
		);
		const handler = registered.get("asr_last_resort_unloaded");
		expect(handler).toBeDefined();
		handler?.({ backend: "whisper" });
		expect(toast.warning).toHaveBeenCalledTimes(1);

		// Simulate a hot-reload / re-mount of the hook consumer: unmount
		// the component and re-render a fresh instance. Pre-fix this
		// RESET the module-level Map, so the same backend re-toasted
		// immediately (HMR while editing the hook would clear the
		// cooldown). Post-fix the timestamps live in the module-scoped
		// Zustand store, which survives re-mounts.
		first.unmount();
		registered.clear();
		renderHook(() => useLastResortUnloadedToast(mockT, onOpenModels));
		const handler2 = registered.get("asr_last_resort_unloaded");
		expect(handler2).toBeDefined();
		handler2?.({ backend: "whisper" });
		expect(toast.warning).toHaveBeenCalledTimes(1);

		// A different backend is still a fresh transition — advance past
		// the dedupe window so the qwen toast isn't collapsed into the
		// (suppressed) whisper one.
		nowSpy.mockReturnValue(1_000_000 + 10_001);
		handler2?.({ backend: "qwen" });
		expect(toast.warning).toHaveBeenCalledTimes(2);
	});
});
