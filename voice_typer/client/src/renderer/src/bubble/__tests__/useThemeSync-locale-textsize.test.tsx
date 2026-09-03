/**
 * Focused tests for `useThemeSync` — locale + text-size reaction.
 *
 * Covers the two sync surfaces added alongside the existing theme
 * triplet handling:
 *
 *   1. `localeChanged` bridge event: the main process pushes the
 *      user's UI locale (`bubble:locale-changed` → preload
 *      `onLocaleChanged` → bridge "localeChanged"); the hook must
 *      route a SUPPORTED locale through the public `setLocale`
 *      (updating `_currentLocale`, `dir`, `lang`, and the i18n
 *      subscribers) and re-render, and must IGNORE unsupported /
 *      garbled payloads (no `dir` flip from a hostile value).
 *   2. `text_size` in the `bubble:config` payload: the hook must set
 *      `--font-scale` = text_size / 14 on `document.documentElement`
 *      — the same formula the main window's `useTheme` applies — and
 *      must ignore non-numeric / non-positive values.
 */
import { act, cleanup, render } from "@testing-library/react";
import { type ReactNode, useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getLocale, setLocale } from "@/i18n/i18n";
import { BubbleBridgeProvider, useBubbleBridge } from "../useBubbleBridge";
import { useThemeSync } from "../useThemeSync";

// ── Mock window.bubble API (same shape as useBubbleBridge.test) ─────
function makeMockBubble() {
	const listeners: Record<string, Array<(payload: unknown) => void>> = {
		show: [],
		hide: [],
		setState: [],
		config: [],
		level: [],
		draggable: [],
		localeChanged: [],
	};
	const api: Record<string, unknown> = {};
	for (const event of [
		"show",
		"hide",
		"setState",
		"config",
		"level",
		"draggable",
		"localeChanged",
	]) {
		const method = `on${event.charAt(0).toUpperCase()}${event.slice(1)}`;
		api[method] = vi.fn((cb: (payload: unknown) => void) => {
			listeners[event]?.push(cb);
			return () => {
				const arr = listeners[event];
				if (arr) {
					listeners[event] = arr.filter((l) => l !== cb);
				}
			};
		});
	}
	return { api, listeners };
}

let mockBubble: ReturnType<typeof makeMockBubble>;

beforeEach(() => {
	mockBubble = makeMockBubble();
	(window as unknown as Record<string, unknown>).bubble =
		mockBubble.api as never;
	localStorage.removeItem("voice-typer-ui-locale");
	// jsdom's documentElement persists across tests in this file —
	// clear the inline --font-scale a previous test may have set so
	// assertions on it start clean.
	document.documentElement.style.removeProperty("--font-scale");
	setLocale("en");
});

afterEach(() => {
	cleanup();
	delete (window as unknown as Record<string, unknown>).bubble;
	localStorage.removeItem("voice-typer-ui-locale");
	setLocale("en");
	vi.restoreAllMocks();
});

// Harness: mounts the hook inside the provider and registers a config
// listener so the config effect body runs (mirrors BubbleInner).
function Harness({ children }: { children?: ReactNode }) {
	const bridge = useBubbleBridge();
	useThemeSync();
	useEffect(() => {
		if (!bridge) return;
		return bridge.on("config", () => {});
	}, [bridge]);
	return <div data-testid="harness">{children}</div>;
}

function renderHarness() {
	return render(
		<BubbleBridgeProvider>
			<Harness />
		</BubbleBridgeProvider>,
	);
}

describe("useThemeSync — localeChanged push", () => {
	it("routes a supported locale through setLocale (locale state, dir, lang)", () => {
		renderHarness();

		act(() => {
			for (const cb of mockBubble.listeners.localeChanged ?? []) cb("de");
		});

		expect(getLocale()).toBe("de");
		expect(document.documentElement.dir).toBe("ltr");
		expect(document.documentElement.lang).toBe("de");
	});

	it("flips dir to rtl for Arabic", () => {
		renderHarness();

		act(() => {
			for (const cb of mockBubble.listeners.localeChanged ?? []) cb("ar");
		});

		expect(getLocale()).toBe("ar");
		expect(document.documentElement.dir).toBe("rtl");
		expect(document.documentElement.lang).toBe("ar");
	});

	it("ignores unsupported / garbled payloads (no dir flip, locale unchanged)", () => {
		renderHarness();
		const warn = vi.spyOn(console, "warn").mockImplementation(() => {});

		act(() => {
			for (const cb of mockBubble.listeners.localeChanged ?? []) cb("<script>");
		});
		// Not a supported locale — setLocale must never run.
		expect(getLocale()).toBe("en");
		expect(document.documentElement.lang).not.toBe("<script>");

		act(() => {
			for (const cb of mockBubble.listeners.localeChanged ?? []) cb({ a: 1 });
		});
		expect(getLocale()).toBe("en");
		expect(warn).not.toHaveBeenCalled();
	});

	it("subscribes EXACTLY ONCE and unsubscribes on unmount", () => {
		const { unmount } = renderHarness();
		expect(mockBubble.api.onLocaleChanged).toHaveBeenCalledTimes(1);
		expect(mockBubble.listeners.localeChanged ?? []).toHaveLength(1);
		unmount();
		expect(mockBubble.listeners.localeChanged ?? []).toHaveLength(0);
	});
});

describe("useThemeSync — text_size from bubble:config", () => {
	it("sets --font-scale to text_size / 14 when the config carries text_size", () => {
		renderHarness();

		act(() => {
			for (const cb of mockBubble.listeners.config ?? []) {
				cb({ text_size: 18 });
			}
		});

		expect(
			document.documentElement.style.getPropertyValue("--font-scale"),
		).toBe(String(18 / 14));
	});

	it("uses the main window's same 14-based formula for the default size", () => {
		renderHarness();

		act(() => {
			for (const cb of mockBubble.listeners.config ?? []) {
				cb({ text_size: 14 });
			}
		});

		expect(
			document.documentElement.style.getPropertyValue("--font-scale"),
		).toBe("1");
	});

	it("keeps the last valid size when a later config omits text_size", () => {
		renderHarness();

		act(() => {
			for (const cb of mockBubble.listeners.config ?? []) {
				cb({ text_size: 16 });
			}
		});
		expect(
			document.documentElement.style.getPropertyValue("--font-scale"),
		).toBe(String(16 / 14));

		act(() => {
			for (const cb of mockBubble.listeners.config ?? []) {
				cb({ theme_mode: "dark" });
			}
		});
		expect(
			document.documentElement.style.getPropertyValue("--font-scale"),
		).toBe(String(16 / 14));
	});

	it("ignores non-numeric and non-positive text_size values", () => {
		// Fresh mounts per payload: the hook keeps the last VALID size
		// in its ref, so invalid payloads must be asserted against a
		// clean instance (a shared instance would carry the previous
		// test's valid size into the assertion).
		renderHarness();
		act(() => {
			for (const cb of mockBubble.listeners.config ?? []) {
				cb({ text_size: "big" });
			}
		});
		expect(
			document.documentElement.style.getPropertyValue("--font-scale"),
		).toBe("");
		cleanup();

		renderHarness();
		act(() => {
			for (const cb of mockBubble.listeners.config ?? []) {
				cb({ text_size: 0 });
			}
		});
		expect(
			document.documentElement.style.getPropertyValue("--font-scale"),
		).toBe("");
	});
});
