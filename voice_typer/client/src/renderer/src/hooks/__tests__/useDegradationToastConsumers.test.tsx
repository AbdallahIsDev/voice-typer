/**
 * Tests for the new degradation/integrity consumer hooks:
 *   - `useCloudFallbackToast` (``cloud_fallback_used``)
 *   - `useHistoryIntegrityToast` (``history_corrupted`` +
 *     ``history_fts5_rebuild_failed``)
 *   - `usePasteDeferredToast` (``paste_deferred``)
 *
 * These events were wired through all 4 protocol layers (allowlist +
 * EVENT_TYPES + TS union + KNOWN_EVENT_TYPES) but had NO renderer
 * subscriber — the documented consumers were dead end-to-end. Each
 * test pins the LIVE contract: the hook subscribes to the event name,
 * surfaces the right localized toast shape, and rate-limits repeat
 * emissions via `degradationToastStore`.
 *
 * Mock strategy mirrors `useDroppedEventConsumers.test.tsx`:
 * `@/hooks/usePython` is mocked to CAPTURE the registered handlers
 * (so tests can fire them directly) and `sonner` is mocked so the
 * toast calls are asserted, not rendered.
 */
import { renderHook } from "@testing-library/react";
import { toast } from "sonner";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useCloudFallbackToast } from "@/hooks/useCloudFallbackToast";
import { useHistoryIntegrityToast } from "@/hooks/useHistoryIntegrityToast";
import { usePasteDeferredToast } from "@/hooks/usePasteDeferredToast";
import { useDegradationToastStore } from "@/stores/degradationToastStore";

// Capture the handlers usePythonEvent registers so we can fire them.
const registered = new Map<string, (data?: unknown) => unknown>();
const mockT = vi.fn((key: string, params?: Record<string, string>) => {
	if (!params || Object.keys(params).length === 0) {
		return key;
	}
	const rendered = Object.entries(params)
		.map(([k, v]) => `${k}=${v}`)
		.join(",");
	return `${key}[${rendered}]`;
});

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
	nowSpy = vi.spyOn(Date, "now").mockReturnValue(1_000_000);
});

afterEach(() => {
	nowSpy.mockRestore();
});

// ── useCloudFallbackToast ────────────────────────────────────────────

describe("useCloudFallbackToast", () => {
	it("registers a handler for cloud_fallback_used", () => {
		renderHook(() => useCloudFallbackToast(mockT));
		expect(registered.has("cloud_fallback_used")).toBe(true);
	});

	it("shows one warning toast naming the local-engine fallback with the provider in the hint", () => {
		renderHook(() => useCloudFallbackToast(mockT));
		registered.get("cloud_fallback_used")?.({
			provider: "openai",
			reason: "HTTP 503 upstream",
		});

		expect(toast.warning).toHaveBeenCalledTimes(1);
		expect(toast.warning).toHaveBeenCalledWith(
			"degradation.cloudFallbackUsed",
			expect.objectContaining({
				id: "cloud-fallback-used",
				description: "degradation.cloudFallbackUsedHint[provider=openai]",
			}),
		);
		// The raw reason stays in the log — the hint is the user-facing copy.
		const call = (toast.warning as ReturnType<typeof vi.fn>).mock.calls[0];
		expect(JSON.stringify(call)).not.toContain("HTTP 503");
	});

	it("falls back to provider 'unknown' when the payload omits it", () => {
		renderHook(() => useCloudFallbackToast(mockT));
		registered.get("cloud_fallback_used")?.({ reason: "timeout" });
		expect(toast.warning).toHaveBeenCalledWith(
			"degradation.cloudFallbackUsed",
			expect.objectContaining({
				description: "degradation.cloudFallbackUsedHint[provider=unknown]",
			}),
		);
	});

	it("collapses a per-transcription outage stream into one toast per 5-minute window", () => {
		renderHook(() => useCloudFallbackToast(mockT));
		const handler = registered.get("cloud_fallback_used");
		handler?.({ provider: "openai", reason: "a" });
		handler?.({ provider: "openai", reason: "b" });
		handler?.({ provider: "openai", reason: "c" });
		expect(toast.warning).toHaveBeenCalledTimes(1);

		// Past the window the next dictation-level failure re-notifies.
		nowSpy.mockReturnValue(1_000_000 + 300_001);
		handler?.({ provider: "openai", reason: "d" });
		expect(toast.warning).toHaveBeenCalledTimes(2);
	});

	it("records the cooldown in the degradation toast store", () => {
		renderHook(() => useCloudFallbackToast(mockT));
		registered.get("cloud_fallback_used")?.({ provider: "openai" });
		expect(useDegradationToastStore.getState().cloudFallbackUsedAt).toBe(
			1_000_000,
		);
	});
});

// ── useHistoryIntegrityToast ──────────────────────────────────────────

describe("useHistoryIntegrityToast", () => {
	it("registers handlers for both history integrity events", () => {
		renderHook(() => useHistoryIntegrityToast(mockT));
		expect(registered.has("history_corrupted")).toBe(true);
		expect(registered.has("history_fts5_rebuild_failed")).toBe(true);
	});

	it("history_corrupted: recovery warning names the recovered count", () => {
		renderHook(() => useHistoryIntegrityToast(mockT));
		registered.get("history_corrupted")?.({
			path: "/home/u/.config/voice-typer/history.db.corrupt",
			db_path: "/home/u/.config/voice-typer/history.db",
			recovered_count: 42,
		});

		expect(toast.warning).toHaveBeenCalledTimes(1);
		expect(toast.warning).toHaveBeenCalledWith(
			"degradation.historyCorrupted",
			expect.objectContaining({
				id: "history-corrupted",
				description: "degradation.historyCorruptedHint[count=42]",
			}),
		);
	});

	it("history_corrupted: a missing/invalid recovered_count degrades to 0, not NaN", () => {
		renderHook(() => useHistoryIntegrityToast(mockT));
		registered.get("history_corrupted")?.({ db_path: "/x/history.db" });
		expect(toast.warning).toHaveBeenCalledWith(
			"degradation.historyCorrupted",
			expect.objectContaining({
				description: "degradation.historyCorruptedHint[count=0]",
			}),
		);
	});

	it("history_fts5_rebuild_failed: privacy warning with the actionable hint", () => {
		renderHook(() => useHistoryIntegrityToast(mockT));
		registered.get("history_fts5_rebuild_failed")?.({
			db_path: "/x/history.db",
			deleted: 5,
			error: "database is locked",
			source: "clear_all",
		});

		expect(toast.warning).toHaveBeenCalledTimes(1);
		expect(toast.warning).toHaveBeenCalledWith(
			"degradation.historyFtsRebuildFailed",
			expect.objectContaining({
				id: "history-fts5-rebuild-failed",
				description: "degradation.historyFtsRebuildFailedHint",
			}),
		);
	});

	it("integrity events have NO cooldown: distinct emissions stay visible (replaced, not suppressed)", () => {
		renderHook(() => useHistoryIntegrityToast(mockT));
		const corrupted = registered.get("history_corrupted");
		corrupted?.({ recovered_count: 1 });
		corrupted?.({ recovered_count: 2 });
		// A repeated corruption report is a NEW integrity event the user
		// should see (the fixed sonner id replaces the in-flight toast).
		expect(toast.warning).toHaveBeenCalledTimes(2);
	});
});

// ── usePasteDeferredToast ────────────────────────────────────────────

describe("usePasteDeferredToast", () => {
	it("registers a handler for paste_deferred", () => {
		renderHook(() => usePasteDeferredToast(mockT));
		expect(registered.has("paste_deferred")).toBe(true);
	});

	it("secure_input: names the actual blocker (Secure Input) in the hint", () => {
		renderHook(() => usePasteDeferredToast(mockT));
		registered.get("paste_deferred")?.({
			reason: "secure_input",
			message: "Paste target has secure input enabled",
		});

		expect(toast.warning).toHaveBeenCalledTimes(1);
		expect(toast.warning).toHaveBeenCalledWith(
			"degradation.pasteDeferred",
			expect.objectContaining({
				id: "paste-deferred",
				description: "degradation.pasteDeferredHintSecureInput",
			}),
		);
	});

	it("ime_composition: names the IME composition blocker in the hint", () => {
		renderHook(() => usePasteDeferredToast(mockT));
		registered.get("paste_deferred")?.({ reason: "ime_composition" });
		expect(toast.warning).toHaveBeenCalledWith(
			"degradation.pasteDeferred",
			expect.objectContaining({
				description: "degradation.pasteDeferredHintIme",
			}),
		);
	});

	it("an unknown reason falls back to the generic paste hint", () => {
		renderHook(() => usePasteDeferredToast(mockT));
		registered.get("paste_deferred")?.({ reason: "future_reason" });
		expect(toast.warning).toHaveBeenCalledWith(
			"degradation.pasteDeferred",
			expect.objectContaining({
				description: "degradation.pasteDeferredHint",
			}),
		);
	});

	it("suppresses back-to-back deferrals inside the 10s window", () => {
		renderHook(() => usePasteDeferredToast(mockT));
		const handler = registered.get("paste_deferred");
		handler?.({ reason: "ime_composition" });
		handler?.({ reason: "ime_composition" });
		expect(toast.warning).toHaveBeenCalledTimes(1);

		nowSpy.mockReturnValue(1_000_000 + 10_001);
		handler?.({ reason: "ime_composition" });
		expect(toast.warning).toHaveBeenCalledTimes(2);
	});

	it("records the cooldown in the degradation toast store", () => {
		renderHook(() => usePasteDeferredToast(mockT));
		registered.get("paste_deferred")?.({ reason: "secure_input" });
		expect(useDegradationToastStore.getState().pasteDeferredAt).toBe(1_000_000);
	});
});
