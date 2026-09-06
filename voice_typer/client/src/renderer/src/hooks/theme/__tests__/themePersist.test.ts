/**
 * Tests for hooks/theme/themePersist — the persistence concern
 * extracted from useTheme.ts (the debounced backend write path, the
 * quit-time flush, and the localStorage cache sync).
 *
 * The write-path semantics pinned here are load-bearing: rapid theme
 * changes must coalesce into ONE ``set_config`` 300ms after the last
 * change (merged pending payload), the flush path must fire
 * synchronously with the merged pending payload, and Promise
 * rejections on both paths must be caught (console.warn, no unhandled
 * rejection).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { clearActiveBridge, setActiveBridge } from "../themeBridge";
import {
	flushPendingThemeSave,
	installBeforeUnloadFlush,
	removeBeforeUnloadFlush,
	resetThemePersistState,
	scheduleThemeSave,
	syncThemeCacheToLocalStorage,
} from "../themePersist";

const callMock = vi.fn();

beforeEach(() => {
	callMock.mockReset();
	callMock.mockResolvedValue(undefined);
	clearActiveBridge();
	resetThemePersistState();
	localStorage.clear();
	vi.useFakeTimers();
});

afterEach(() => {
	removeBeforeUnloadFlush();
	vi.useRealTimers();
});

describe("themePersist — scheduleThemeSave (debounced write path)", () => {
	it("does NOT call set_config before the 300ms debounce elapses", async () => {
		setActiveBridge(callMock, vi.fn());
		scheduleThemeSave({ theme_mode: "dark" });
		vi.advanceTimersByTime(299);
		expect(callMock).not.toHaveBeenCalled();
		await vi.advanceTimersByTimeAsync(1);
		expect(callMock).toHaveBeenCalledOnce();
	});

	it("coalesces rapid changes into ONE set_config with the LAST merged payload", async () => {
		setActiveBridge(callMock, vi.fn());
		scheduleThemeSave({ theme_mode: "dark" });
		scheduleThemeSave({ theme_mode: "light" });
		scheduleThemeSave({ text_size: 18, theme_mode: "system" });

		await vi.advanceTimersByTimeAsync(300);

		expect(callMock).toHaveBeenCalledOnce();
		expect(callMock).toHaveBeenCalledWith("set_config", {
			theme_mode: "system",
			text_size: 18,
		});
	});

	it("reads the bridge at FIRE time, not schedule time", async () => {
		// Schedule BEFORE any bridge is registered…
		scheduleThemeSave({ theme_mode: "dark" });
		// …then register the bridge — the pending save must still fire.
		setActiveBridge(callMock, vi.fn());
		await vi.advanceTimersByTimeAsync(300);
		expect(callMock).toHaveBeenCalledWith("set_config", {
			theme_mode: "dark",
		});
	});

	it("drops the write when no bridge is registered by fire time", async () => {
		scheduleThemeSave({ theme_mode: "dark" });
		await vi.advanceTimersByTimeAsync(300);
		expect(callMock).not.toHaveBeenCalled();
	});

	it("catches a rejected set_config (console.warn, no unhandled rejection)", async () => {
		const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
		setActiveBridge(callMock, vi.fn());
		callMock.mockRejectedValueOnce(new Error("backend down"));

		scheduleThemeSave({ theme_mode: "dark" });
		await vi.advanceTimersByTimeAsync(300);

		const warn = warnSpy.mock.calls.find((c) =>
			String(c[0] ?? "").includes(
				"[renderer:useTheme] set_config (debounced) failed",
			),
		);
		expect(warn).toBeTruthy();
		warnSpy.mockRestore();
	});
});

describe("themePersist — flushPendingThemeSave", () => {
	it("fires the pending save synchronously and cancels the debounce", async () => {
		setActiveBridge(callMock, vi.fn());
		scheduleThemeSave({ theme_mode: "dark" });
		scheduleThemeSave({ text_size: 16 });

		flushPendingThemeSave();

		expect(callMock).toHaveBeenCalledOnce();
		expect(callMock).toHaveBeenCalledWith("set_config", {
			theme_mode: "dark",
			text_size: 16,
		});

		// The debounce timer was cancelled — advancing time must
		// NOT produce a second write.
		await vi.advanceTimersByTimeAsync(500);
		expect(callMock).toHaveBeenCalledOnce();
	});

	it("clears the pending payload so a later debounce/flush is a no-op", async () => {
		setActiveBridge(callMock, vi.fn());
		scheduleThemeSave({ theme_mode: "dark" });
		flushPendingThemeSave();
		expect(callMock).toHaveBeenCalledOnce();

		flushPendingThemeSave();
		await vi.advanceTimersByTimeAsync(500);
		expect(callMock).toHaveBeenCalledOnce();
	});

	it("is a no-op when nothing is pending", () => {
		flushPendingThemeSave();
		expect(callMock).not.toHaveBeenCalled();
	});

	it("catches a rejected flush-time set_config", async () => {
		const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
		setActiveBridge(callMock, vi.fn());
		callMock.mockRejectedValueOnce(new Error("backend down"));

		scheduleThemeSave({ theme_mode: "dark" });
		flushPendingThemeSave();
		await Promise.resolve();
		await Promise.resolve();

		const warn = warnSpy.mock.calls.find((c) =>
			String(c[0] ?? "").includes(
				"[renderer:useTheme] set_config (flush) failed",
			),
		);
		expect(warn).toBeTruthy();
		warnSpy.mockRestore();
	});
});

describe("themePersist — resetThemePersistState", () => {
	it("drops the pending payload so flush after reset is a no-op", () => {
		setActiveBridge(callMock, vi.fn());
		scheduleThemeSave({ theme_mode: "dark" });
		resetThemePersistState();
		flushPendingThemeSave();
		expect(callMock).not.toHaveBeenCalled();
	});
});

describe("themePersist — syncThemeCacheToLocalStorage", () => {
	it("writes all four cache keys", () => {
		syncThemeCacheToLocalStorage(
			"dark",
			"nord",
			{ light: { a: "1" }, dark: { a: "2" } },
			16,
		);
		expect(localStorage.getItem("voice-typer-theme-mode")).toBe("dark");
		expect(localStorage.getItem("voice-typer-theme-preset")).toBe("nord");
		expect(localStorage.getItem("voice-typer-text-size")).toBe("16");
		const raw = localStorage.getItem("voice-typer-custom-theme");
		expect(raw).toBe(JSON.stringify({ light: { a: "1" }, dark: { a: "2" } }));
	});

	it("removes the custom-theme key when the custom map is null", () => {
		localStorage.setItem("voice-typer-custom-theme", '{"stale":true}');
		syncThemeCacheToLocalStorage("system", "default", null, 14);
		expect(localStorage.getItem("voice-typer-custom-theme")).toBeNull();
	});

	it("warns (and keeps going) when localStorage is unavailable", () => {
		const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
		// Swap the localStorage global for a throwing stub — the
		// test-setup fallback storage is a plain object, so spying
		// on Storage.prototype would not intercept it.
		const lsDesc = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
		Object.defineProperty(globalThis, "localStorage", {
			configurable: true,
			value: {
				getItem: () => null,
				setItem: () => {
					throw new Error("quota exceeded");
				},
				removeItem: () => {},
				clear: () => {},
			},
		});

		try {
			syncThemeCacheToLocalStorage("dark", "default", null, 14);
		} finally {
			if (lsDesc) {
				Object.defineProperty(globalThis, "localStorage", lsDesc);
			}
		}

		const warn = warnSpy.mock.calls.find((c) =>
			String(c[0] ?? "").includes(
				"[renderer:useTheme] localStorage sync failed",
			),
		);
		expect(warn).toBeTruthy();
		warnSpy.mockRestore();
	});
});

describe("themePersist — beforeunload flush listener", () => {
	it("install/remove add and remove the same listener", () => {
		const addSpy = vi.spyOn(window, "addEventListener");
		const removeSpy = vi.spyOn(window, "removeEventListener");

		installBeforeUnloadFlush();
		expect(addSpy).toHaveBeenCalledWith("beforeunload", flushPendingThemeSave);
		removeBeforeUnloadFlush();
		expect(removeSpy).toHaveBeenCalledWith(
			"beforeunload",
			flushPendingThemeSave,
		);

		addSpy.mockRestore();
		removeSpy.mockRestore();
	});
});
