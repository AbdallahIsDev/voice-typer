/**
 * Tests for useNavigateEvent (extracted from App.tsx).
 *
 * Contract: subscribe to the backend ``navigate`` push event and route
 * it through the shared navigation store — validating the page against
 * the route table, deep-linking Settings consent rows to the Privacy
 * sub-page, and warning (without routing) on unknown paths.
 */
import { renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useNavigateEvent } from "@/hooks/useNavigateEvent";

// Capture the handler usePythonEvent registers so we can fire it.
const registered = new Map<string, (data?: unknown) => unknown>();
const mockNavigate = vi.fn();

vi.mock("@/hooks/usePython", () => ({
	usePythonEvent: (type: string, handler: (data?: unknown) => unknown) => {
		registered.set(type, handler);
	},
}));

const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});

/** Return the navigate handler (asserting it was registered). */
function getHandler(): (data?: unknown) => unknown {
	const handler = registered.get("navigate");
	if (!handler) throw new Error("navigate handler was not registered");
	return handler;
}

beforeEach(() => {
	registered.clear();
	vi.clearAllMocks();
});

afterEach(() => {
	warnSpy.mockClear();
});

describe("useNavigateEvent", () => {
	it("registers a handler for the navigate event", () => {
		renderHook(() => useNavigateEvent({ navigate: mockNavigate }));
		expect(registered.has("navigate")).toBe(true);
	});

	it("routes a known page path through navigate", () => {
		renderHook(() => useNavigateEvent({ navigate: mockNavigate }));
		getHandler()({ path: "/history" });
		expect(mockNavigate).toHaveBeenCalledWith("history", undefined);
	});

	it("accepts a path without the leading slash", () => {
		renderHook(() => useNavigateEvent({ navigate: mockNavigate }));
		getHandler()({ path: "onboarding" });
		expect(mockNavigate).toHaveBeenCalledWith("onboarding", undefined);
	});

	it("deep-links a consent_field on the legacy settings literal to settingsPrivacy", () => {
		renderHook(() => useNavigateEvent({ navigate: mockNavigate }));
		getHandler()({
			path: "/settings",
			consent_field: "voice_biometric_consent",
		});
		expect(mockNavigate).toHaveBeenCalledWith("settingsPrivacy", {
			consentField: "voice_biometric_consent",
		});
	});

	it("passes an explicit settings sub-page through unchanged with a consent_field", () => {
		renderHook(() => useNavigateEvent({ navigate: mockNavigate }));
		getHandler()({
			path: "/settingsAI",
			consent_field: "cloud_groq_consent",
		});
		expect(mockNavigate).toHaveBeenCalledWith("settingsAI", {
			consentField: "cloud_groq_consent",
		});
	});

	it("warns and does NOT navigate for an unknown page path", () => {
		renderHook(() => useNavigateEvent({ navigate: mockNavigate }));
		getHandler()({ path: "/definitely-not-a-page" });
		expect(mockNavigate).not.toHaveBeenCalled();
		expect(warnSpy).toHaveBeenCalledTimes(1);
		expect(warnSpy.mock.calls[0]?.[0]).toContain("definitely-not-a-page");
	});

	it("ignores payloads without a string path", () => {
		renderHook(() => useNavigateEvent({ navigate: mockNavigate }));
		getHandler()({});
		getHandler()({ path: 42 });
		expect(mockNavigate).not.toHaveBeenCalled();
		expect(warnSpy).not.toHaveBeenCalled();
	});

	it("ignores null/undefined payloads", () => {
		renderHook(() => useNavigateEvent({ navigate: mockNavigate }));
		getHandler()(undefined);
		getHandler()(null);
		expect(mockNavigate).not.toHaveBeenCalled();
	});
});
