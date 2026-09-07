/**
 * Tests for useLastTranscriptionPreview (extracted from Home.tsx).
 *
 * Contract: own the ephemeral last-transcription preview state — text +
 * confidence summary + auto-clear timer — plus the preview card's
 * undo/repaste/discard actions and the `recording_started` reset.
 * Behaviour must match the original inline Home.tsx handlers
 * statement-for-statement.
 */
import { act, renderHook } from "@testing-library/react";
import { toast } from "sonner";
import type { Mock } from "vitest";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { PythonCall } from "@/hooks/usePython";
import { useLastTranscriptionPreview } from "@/pages/home/hooks/useLastTranscriptionPreview";
import { LAST_TEXT_AUTO_CLEAR_MS } from "@/pages/home/lib/constants";
import type { TranscriptionQualitySummary } from "@/types/ipc";

// Capture the handler usePythonEvent registers so we can fire it.
const registered = new Map<string, (data?: unknown) => unknown>();
// Bridge-call test double shaped as PythonCall (no real bridge in unit tests).
const mockCall = vi.fn() as unknown as PythonCall & Mock;
const celebrate = vi.fn();

vi.mock("@/hooks/usePython", () => ({
	usePythonEvent: (type: string, handler: (data?: unknown) => unknown) => {
		registered.set(type, handler);
	},
}));

vi.mock("@/i18n/i18n", () => ({
	t: (key: string) => key,
}));

vi.mock("sonner", () => ({
	toast: { error: vi.fn(), success: vi.fn() },
}));

const qualitySample: TranscriptionQualitySummary = { mean_logprob: -0.4 };

function renderPreview() {
	return renderHook(() => useLastTranscriptionPreview(mockCall, celebrate));
}

/** Fire the recording_started handler (asserting it was registered). */
function fireRecordingStarted() {
	const handler = registered.get("recording_started");
	if (!handler) throw new Error("recording_started handler not registered");
	act(() => {
		handler();
	});
}

beforeEach(() => {
	registered.clear();
	vi.clearAllMocks();
	vi.useFakeTimers();
});

afterEach(() => {
	vi.useRealTimers();
});

describe("useLastTranscriptionPreview", () => {
	it("registers the recording_started subscription", () => {
		renderPreview();
		expect(registered.has("recording_started")).toBe(true);
	});

	it("ignores transcription_final payloads without usable text", () => {
		const { result } = renderPreview();
		act(() => {
			result.current.applyTranscriptionFinal({ text: "   " });
		});
		act(() => {
			result.current.applyTranscriptionFinal(undefined);
		});
		expect(result.current.lastText).toBe("");
		expect(result.current.lastQuality).toBeUndefined();
		expect(celebrate).not.toHaveBeenCalled();
	});

	it("stores text + quality and celebrates on a usable transcription_final", () => {
		const { result } = renderPreview();
		const quality = qualitySample;
		act(() => {
			result.current.applyTranscriptionFinal({
				text: "hello world",
				quality,
			});
		});
		expect(result.current.lastText).toBe("hello world");
		expect(result.current.lastQuality).toBe(quality);
		expect(celebrate).toHaveBeenCalledTimes(1);
	});

	it("defaults quality to undefined when the payload omits it", () => {
		const { result } = renderPreview();
		act(() => {
			result.current.applyTranscriptionFinal({
				text: "no quality",
				quality: { mean_logprob: -1 },
			});
		});
		act(() => {
			result.current.applyTranscriptionFinal({ text: "no quality" });
		});
		expect(result.current.lastText).toBe("no quality");
		expect(result.current.lastQuality).toBeUndefined();
	});

	it("auto-clears the preview after LAST_TEXT_AUTO_CLEAR_MS", () => {
		const { result } = renderPreview();
		act(() => {
			result.current.applyTranscriptionFinal({ text: "temporary" });
		});
		expect(result.current.lastText).toBe("temporary");
		act(() => {
			vi.advanceTimersByTime(LAST_TEXT_AUTO_CLEAR_MS - 1);
		});
		expect(result.current.lastText).toBe("temporary");
		act(() => {
			vi.advanceTimersByTime(1);
		});
		expect(result.current.lastText).toBe("");
		expect(result.current.lastQuality).toBeUndefined();
	});

	it("restarts the auto-clear timer on each accepted payload", () => {
		const { result } = renderPreview();
		act(() => {
			result.current.applyTranscriptionFinal({ text: "first" });
		});
		act(() => {
			vi.advanceTimersByTime(LAST_TEXT_AUTO_CLEAR_MS - 1);
		});
		act(() => {
			result.current.applyTranscriptionFinal({ text: "second" });
		});
		act(() => {
			vi.advanceTimersByTime(LAST_TEXT_AUTO_CLEAR_MS - 1);
		});
		expect(result.current.lastText).toBe("second");
		act(() => {
			vi.advanceTimersByTime(1);
		});
		expect(result.current.lastText).toBe("");
	});

	it("recording_started clears the text (and timer) but keeps quality", () => {
		const { result } = renderPreview();
		act(() => {
			result.current.applyTranscriptionFinal({
				text: "kept quality",
				quality: { mean_logprob: -0.2 },
			});
		});
		fireRecordingStarted();
		expect(result.current.lastText).toBe("");
		expect(result.current.lastQuality).not.toBeUndefined();
		// The pending auto-clear timer must be gone: advancing time no
		// longer fires a state change (no quality wipe).
		act(() => {
			vi.advanceTimersByTime(LAST_TEXT_AUTO_CLEAR_MS * 2);
		});
		expect(result.current.lastQuality).not.toBeUndefined();
	});

	it("undo calls undo_last then clears the preview", async () => {
		mockCall.mockResolvedValue(undefined);
		const { result } = renderPreview();
		act(() => {
			result.current.applyTranscriptionFinal({ text: "to undo" });
		});
		await act(async () => {
			await result.current.handleUndo();
		});
		expect(mockCall).toHaveBeenCalledWith("undo_last");
		expect(result.current.lastText).toBe("");
	});

	it("undo surfaces a toast on IPC failure and still clears the preview", async () => {
		mockCall.mockRejectedValue(new Error("bridge down"));
		const { result } = renderPreview();
		act(() => {
			result.current.applyTranscriptionFinal({ text: "undo fails" });
		});
		await act(async () => {
			await result.current.handleUndo();
		});
		expect(toast.error).toHaveBeenCalledWith("home.undoFailed");
		expect(result.current.lastText).toBe("");
	});

	it("repaste surfaces a toast on IPC failure", async () => {
		mockCall.mockRejectedValue(new Error("bridge down"));
		const { result } = renderPreview();
		await act(async () => {
			await result.current.handleRepaste();
		});
		expect(mockCall).toHaveBeenCalledWith("repaste_last");
		expect(toast.error).toHaveBeenCalledWith("home.repasteFailed");
	});

	it("discard clears text AND quality immediately", () => {
		const { result } = renderPreview();
		act(() => {
			result.current.applyTranscriptionFinal({
				text: "to discard",
				quality: { mean_logprob: -0.9 },
			});
		});
		act(() => {
			result.current.handleDiscard();
		});
		expect(result.current.lastText).toBe("");
		expect(result.current.lastQuality).toBeUndefined();
	});

	it("clears the pending auto-clear timer on unmount", () => {
		const { result, unmount } = renderPreview();
		act(() => {
			result.current.applyTranscriptionFinal({ text: "outlives mount" });
		});
		unmount();
		// No state updates fire after unmount — advancing past the timer
		// must not warn about updates on unmounted components.
		expect(() => {
			vi.advanceTimersByTime(LAST_TEXT_AUTO_CLEAR_MS * 2);
		}).not.toThrow();
	});
});
