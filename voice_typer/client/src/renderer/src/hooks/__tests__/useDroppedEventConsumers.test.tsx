/**
 * Tests for the previously-dropped-event consumer hooks:
 *   - `useAsrBackendLoadToast` (``asr_backend_ready`` /
 *     ``asr_backend_load_failed``)
 *   - `useMicPermissionRevokedToast` (``microphone_permission_revoked``)
 *   - `useMicrophoneDisconnectedToast` (``microphone_disconnected``)
 *   - `useTrayFallbackToast` (``tray_fallback_notification``)
 *
 * These events were published by the Python sidecar but dropped at the
 * host's event allowlist gate (or delivered with no subscriber), so the
 * documented consumer contracts were dead end-to-end. Each test pins
 * the LIVE contract: the hook subscribes to the event name, surfaces
 * the right toast shape, and rate-limits repeat emissions.
 *
 * Cooldown state lives in `degradationToastStore` (Zustand, outside the
 * hook modules — HMR-safe), so the reset seam is the store's
 * `resetForTest`, exercised in `beforeEach`.
 *
 * Mock strategy mirrors `useLastResortUnloadedToast.test.tsx`:
 * `@/hooks/usePython` is mocked to CAPTURE the registered handlers
 * (so tests can fire them directly) and `sonner` is mocked so the
 * toast calls are asserted, not rendered.
 */
import { renderHook } from "@testing-library/react";
import { toast } from "sonner";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
	capToastDescription,
	TOAST_DESCRIPTION_MAX_CHARS,
} from "@/hooks/capToastDescription";
import { useAsrBackendLoadToast } from "@/hooks/useAsrBackendLoadToast";
import { useMicPermissionRevokedToast } from "@/hooks/useMicPermissionRevokedToast";
import { useMicrophoneDisconnectedToast } from "@/hooks/useMicrophoneDisconnectedToast";
import { useTrayFallbackToast } from "@/hooks/useTrayFallbackToast";
import { useDegradationToastStore } from "@/stores/degradationToastStore";
import { useDeviceLostStore } from "@/stores/deviceLostStore";

// Capture the handlers usePythonEvent registers so we can fire them.
const registered = new Map<string, (data?: unknown) => unknown>();
const mockT = vi.fn((key: string, params?: Record<string, string>) =>
	params?.name ? `${key}(${params.name})` : key,
);
const onOpenModels = vi.fn();
const onOpenMicrophone = vi.fn();

vi.mock("@/hooks/usePython", () => ({
	usePythonEvent: (type: string, handler: (data?: unknown) => unknown) => {
		registered.set(type, handler);
	},
}));

vi.mock("sonner", () => ({
	toast: {
		error: vi.fn(),
		warning: vi.fn(),
		info: vi.fn(),
		success: vi.fn(),
		dismiss: vi.fn(),
	},
}));

let nowSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
	registered.clear();
	vi.clearAllMocks();
	useDegradationToastStore.getState().resetForTest();
	useDeviceLostStore.getState().resetForTest();
	nowSpy = vi.spyOn(Date, "now").mockReturnValue(1_000_000);
});

afterEach(() => {
	nowSpy.mockRestore();
});

// ── useAsrBackendLoadToast ───────────────────────────────────────────

describe("useAsrBackendLoadToast", () => {
	it("registers handlers for both backend-load lifecycle events", () => {
		renderHook(() => useAsrBackendLoadToast(mockT, onOpenModels));
		expect(registered.has("asr_backend_load_failed")).toBe(true);
		expect(registered.has("asr_backend_ready")).toBe(true);
	});

	it("shows an error toast naming the model + failure reason with an Open Models action", () => {
		renderHook(() => useAsrBackendLoadToast(mockT, onOpenModels));
		registered.get("asr_backend_load_failed")?.({
			backend: "whisper",
			model_size: "base",
			failure_reason: "load_active returned falsy",
		});

		expect(toast.error).toHaveBeenCalledTimes(1);
		expect(toast.error).toHaveBeenCalledWith(
			"models.snack.loadFailed(base)",
			expect.objectContaining({
				id: "asr-backend-load-failed",
				description: "load_active returned falsy",
				action: { label: "common.openModels", onClick: onOpenModels },
			}),
		);
	});

	it("caps an unbounded failure_reason to the toast-description budget", () => {
		renderHook(() => useAsrBackendLoadToast(mockT, onOpenModels));
		const longReason = "x".repeat(TOAST_DESCRIPTION_MAX_CHARS * 5);
		registered.get("asr_backend_load_failed")?.({
			backend: "whisper",
			model_size: "base",
			failure_reason: longReason,
		});

		expect(toast.error).toHaveBeenCalledTimes(1);
		const description = (toast.error as ReturnType<typeof vi.fn>).mock
			.calls[0]?.[1]?.description as string;
		expect(description.length).toBe(TOAST_DESCRIPTION_MAX_CHARS);
		expect(description.endsWith("…")).toBe(true);
		expect(description.startsWith("x")).toBe(true);
	});

	it("falls back to the backend name when the payload has no model_size", () => {
		renderHook(() => useAsrBackendLoadToast(mockT, onOpenModels));
		registered.get("asr_backend_load_failed")?.({ backend: "qwen" });
		expect(toast.error).toHaveBeenCalledWith(
			"models.snack.loadFailed(qwen)",
			expect.any(Object),
		);
	});

	it("dismisses the failure toast when asr_backend_ready arrives (the load succeeded)", () => {
		renderHook(() => useAsrBackendLoadToast(mockT, onOpenModels));
		registered.get("asr_backend_load_failed")?.({
			backend: "whisper",
			model_size: "base",
			failure_reason: "boom",
		});
		registered.get("asr_backend_ready")?.({
			backend: "whisper",
			model_size: "base",
		});

		// The ready event clears the failure surface and shows NO extra
		// toast (success is already surfaced by the Models page + the
		// status pill).
		expect(toast.dismiss).toHaveBeenCalledWith("asr-backend-load-failed");
		expect(toast.success).not.toHaveBeenCalled();
		expect(toast.error).toHaveBeenCalledTimes(1);
	});

	it("suppresses back-to-back load failures inside the cooldown window", () => {
		renderHook(() => useAsrBackendLoadToast(mockT, onOpenModels));
		const handler = registered.get("asr_backend_load_failed");
		handler?.({ backend: "whisper", model_size: "base", failure_reason: "a" });
		handler?.({ backend: "whisper", model_size: "base", failure_reason: "b" });
		expect(toast.error).toHaveBeenCalledTimes(1);

		// Past the window, a new failure re-toasts.
		nowSpy.mockReturnValue(1_000_000 + 10_001);
		handler?.({ backend: "whisper", model_size: "base", failure_reason: "c" });
		expect(toast.error).toHaveBeenCalledTimes(2);
	});

	it("records the cooldown in the degradation toast store (HMR-safe state)", () => {
		renderHook(() => useAsrBackendLoadToast(mockT, onOpenModels));
		registered.get("asr_backend_load_failed")?.({
			backend: "whisper",
			model_size: "base",
			failure_reason: "a",
		});
		expect(useDegradationToastStore.getState().asrBackendLoadFailedAt).toBe(
			1_000_000,
		);
	});
});

// ── useMicPermissionRevokedToast ─────────────────────────────────────

describe("useMicPermissionRevokedToast", () => {
	it("registers a handler for microphone_permission_revoked", () => {
		renderHook(() => useMicPermissionRevokedToast(mockT));
		expect(registered.has("microphone_permission_revoked")).toBe(true);
	});

	it("shows the distinct 'Mic permission revoked' warning banner (reuses the localized bubble label)", () => {
		renderHook(() => useMicPermissionRevokedToast(mockT));
		registered.get("microphone_permission_revoked")?.();

		expect(toast.warning).toHaveBeenCalledTimes(1);
		expect(toast.warning).toHaveBeenCalledWith(
			"bubble.permissionRevokedLabel",
			expect.objectContaining({ id: "mic-permission-revoked" }),
		);
	});

	it("suppresses re-emissions inside the cooldown window", () => {
		renderHook(() => useMicPermissionRevokedToast(mockT));
		const handler = registered.get("microphone_permission_revoked");
		handler?.();
		handler?.();
		expect(toast.warning).toHaveBeenCalledTimes(1);

		nowSpy.mockReturnValue(1_000_000 + 10_001);
		handler?.();
		expect(toast.warning).toHaveBeenCalledTimes(2);
	});
});

// ── useMicrophoneDisconnectedToast ───────────────────────────────────

describe("useMicrophoneDisconnectedToast", () => {
	it("registers a handler for microphone_disconnected", () => {
		renderHook(() => useMicrophoneDisconnectedToast(mockT, onOpenMicrophone));
		expect(registered.has("microphone_disconnected")).toBe(true);
	});

	it("routes the loss to the SHARED device-lost surface (store + same toast id + Microphone action)", () => {
		renderHook(() => useMicrophoneDisconnectedToast(mockT, onOpenMicrophone));
		registered.get("microphone_disconnected")?.();

		const store = useDeviceLostStore.getState();
		expect(store.lostSource).toBe("recording_stream");

		expect(toast.warning).toHaveBeenCalledTimes(1);
		expect(toast.warning).toHaveBeenCalledWith(
			"degradation.deviceLost",
			expect.objectContaining({
				id: "device-lost",
				description: "degradation.deviceLostHint",
				action: { label: "nav.microphone", onClick: onOpenMicrophone },
			}),
		);
	});

	it("shares the device_lost dedupe clock: a loss recorded during the window does not re-toast", () => {
		renderHook(() => useMicrophoneDisconnectedToast(mockT, onOpenMicrophone));
		const handler = registered.get("microphone_disconnected");
		handler?.();
		handler?.();
		expect(toast.warning).toHaveBeenCalledTimes(1);
		// The store still records the latest loss even when suppressed.
		expect(useDeviceLostStore.getState().lostSource).toBe("recording_stream");
	});
});

// ── useTrayFallbackToast ─────────────────────────────────────────────

describe("useTrayFallbackToast", () => {
	it("registers a handler for tray_fallback_notification", () => {
		renderHook(() => useTrayFallbackToast(mockT));
		expect(registered.has("tray_fallback_notification")).toBe(true);
	});

	it("shows the generic tray-unavailable banner when the payload is empty", () => {
		renderHook(() => useTrayFallbackToast(mockT));
		registered.get("tray_fallback_notification")?.();

		expect(toast.warning).toHaveBeenCalledTimes(1);
		expect(toast.warning).toHaveBeenCalledWith(
			"degradation.trayUnavailable",
			expect.objectContaining({ id: "tray-fallback-notification" }),
		);
	});

	it("uses the nested title/message as the description (the emitter's canonical data envelope)", () => {
		renderHook(() => useTrayFallbackToast(mockT));
		registered.get("tray_fallback_notification")?.({
			title: "Backup finished",
			message: "History recovered",
		});
		expect(toast.warning).toHaveBeenCalledWith(
			"degradation.trayUnavailable",
			expect.objectContaining({
				description: "Backup finished: History recovered",
			}),
		);
	});

	it("caps a tray title/message pair that exceeds the description budget", () => {
		renderHook(() => useTrayFallbackToast(mockT));
		registered.get("tray_fallback_notification")?.({
			title: "t".repeat(TOAST_DESCRIPTION_MAX_CHARS),
			message: "m".repeat(TOAST_DESCRIPTION_MAX_CHARS),
		});
		const description = (toast.warning as ReturnType<typeof vi.fn>).mock
			.calls[0]?.[1]?.description as string;
		expect(description.length).toBe(TOAST_DESCRIPTION_MAX_CHARS);
		expect(description.endsWith("…")).toBe(true);
	});

	it("collapses a backlog drain into ONE banner inside the 60s window", () => {
		renderHook(() => useTrayFallbackToast(mockT));
		const handler = registered.get("tray_fallback_notification");
		handler?.();
		handler?.();
		handler?.();
		expect(toast.warning).toHaveBeenCalledTimes(1);
	});
});

// ── capToastDescription helper ───────────────────────────────────────

describe("capToastDescription", () => {
	it("passes short strings through unchanged", () => {
		expect(capToastDescription("boom")).toBe("boom");
	});

	it("caps at exactly the budget with an ellipsis", () => {
		const out = capToastDescription(
			"y".repeat(TOAST_DESCRIPTION_MAX_CHARS + 40),
		);
		expect(out.length).toBe(TOAST_DESCRIPTION_MAX_CHARS);
		expect(out.endsWith("…")).toBe(true);
	});

	it("leaves budget-length strings untouched (boundary)", () => {
		const exact = "z".repeat(TOAST_DESCRIPTION_MAX_CHARS);
		expect(capToastDescription(exact)).toBe(exact);
	});
});
