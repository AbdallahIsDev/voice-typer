/**
 * Tests for useForceCancel (extracted from Home.tsx).
 *
 * Contract: the "Force cancel" availability state machine — reveal the
 * affordance only after FORCE_CANCEL_DELAY_MS inside "transcribing",
 * reset on every other status, stay in sync with the store's
 * recordingState, and run the `force_cancel_transcription` IPC with
 * success/failure toasts. The reveal/reset semantics are
 * consent/privacy-sensitive surface wiring and must match the original
 * inline Home.tsx implementation exactly.
 */
import { act, renderHook } from "@testing-library/react";
import { toast } from "sonner";
import type { Mock } from "vitest";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CallFn } from "@/pages/home/hooks/useFirstRecordingCelebration";
import { useForceCancel } from "@/pages/home/hooks/useForceCancel";
import { FORCE_CANCEL_DELAY_MS } from "@/pages/home/lib/constants";
import { useAppStore } from "@/stores/appStore";

// Bridge-call test double shaped as CallFn (no real bridge in unit tests).
const mockCall = vi.fn() as unknown as CallFn & Mock;

vi.mock("@/i18n/i18n", () => ({
	t: (key: string) => key,
}));

vi.mock("sonner", () => ({
	toast: { error: vi.fn(), success: vi.fn() },
}));

function renderForceCancel() {
	return renderHook(() => useForceCancel(mockCall));
}

beforeEach(() => {
	vi.clearAllMocks();
	vi.useFakeTimers();
	useAppStore.setState({ recordingState: "idle" });
});

afterEach(() => {
	vi.useRealTimers();
});

describe("useForceCancel", () => {
	it("keeps the affordance hidden while transcribing before the delay", () => {
		const { result } = renderForceCancel();
		act(() => {
			result.current.applyStatusChange({ status: "transcribing" });
		});
		expect(result.current.showForceCancel).toBe(false);
	});

	it("reveals the affordance after FORCE_CANCEL_DELAY_MS in transcribing", () => {
		const { result } = renderForceCancel();
		act(() => {
			result.current.applyStatusChange({ status: "transcribing" });
		});
		act(() => {
			vi.advanceTimersByTime(FORCE_CANCEL_DELAY_MS - 1);
		});
		expect(result.current.showForceCancel).toBe(false);
		act(() => {
			vi.advanceTimersByTime(1);
		});
		expect(result.current.showForceCancel).toBe(true);
	});

	it("stamps the transcribe start once across repeated transcribing events", () => {
		const { result } = renderForceCancel();
		act(() => {
			result.current.applyStatusChange({ status: "transcribing" });
		});
		act(() => {
			vi.advanceTimersByTime(FORCE_CANCEL_DELAY_MS - 1);
		});
		// A duplicate transcribing event must NOT restart the clock.
		act(() => {
			result.current.applyStatusChange({ status: "transcribing" });
		});
		act(() => {
			vi.advanceTimersByTime(1);
		});
		expect(result.current.showForceCancel).toBe(true);
	});

	it("hides the affordance and clears the stamp on a non-transcribing status", () => {
		const { result } = renderForceCancel();
		act(() => {
			result.current.applyStatusChange({ status: "transcribing" });
		});
		act(() => {
			vi.advanceTimersByTime(FORCE_CANCEL_DELAY_MS);
		});
		expect(result.current.showForceCancel).toBe(true);
		act(() => {
			result.current.applyStatusChange({ status: "idle" });
		});
		expect(result.current.showForceCancel).toBe(false);
		// The stamp is cleared: re-entering transcribing restarts the delay.
		act(() => {
			result.current.applyStatusChange({ status: "transcribing" });
		});
		act(() => {
			vi.advanceTimersByTime(FORCE_CANCEL_DELAY_MS - 1);
		});
		expect(result.current.showForceCancel).toBe(false);
	});

	it("tolerates malformed status payloads (treated as non-transcribing)", () => {
		const { result } = renderForceCancel();
		act(() => {
			result.current.applyStatusChange(undefined);
		});
		act(() => {
			vi.advanceTimersByTime(FORCE_CANCEL_DELAY_MS);
		});
		expect(result.current.showForceCancel).toBe(false);
	});

	it("syncs with the store when the page mounts mid-transcription", () => {
		useAppStore.setState({ recordingState: "transcribing" });
		const { result } = renderForceCancel();
		act(() => {
			vi.advanceTimersByTime(FORCE_CANCEL_DELAY_MS);
		});
		expect(result.current.showForceCancel).toBe(true);
	});

	it("hides the affordance when the store leaves transcribing", () => {
		useAppStore.setState({ recordingState: "transcribing" });
		const { result } = renderForceCancel();
		act(() => {
			vi.advanceTimersByTime(FORCE_CANCEL_DELAY_MS);
		});
		expect(result.current.showForceCancel).toBe(true);
		act(() => {
			useAppStore.setState({ recordingState: "idle" });
		});
		expect(result.current.showForceCancel).toBe(false);
	});

	it("handleForceCancel calls the IPC and toasts success", async () => {
		mockCall.mockResolvedValue(undefined);
		const { result } = renderForceCancel();
		await act(async () => {
			await result.current.handleForceCancel();
		});
		expect(mockCall).toHaveBeenCalledWith("force_cancel_transcription");
		expect(toast.success).toHaveBeenCalledWith("home.forceCancel");
	});

	it("handleForceCancel toasts an error on IPC failure", async () => {
		mockCall.mockRejectedValue(new Error("bridge down"));
		const { result } = renderForceCancel();
		await act(async () => {
			await result.current.handleForceCancel();
		});
		expect(toast.error).toHaveBeenCalledWith("home.forceCancelFailed");
	});
});
