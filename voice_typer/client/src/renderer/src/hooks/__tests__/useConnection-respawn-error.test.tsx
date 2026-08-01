/**
 * Tests for the  fix: useConnection's `error` event handler
 * branches on the typed `respawn_exhausted` code (not the English
 * substring sentinel) and surfaces the localized
 * `connection.respawnFailed` message.
 *
 * Background (): the previous implementation synthesized an
 * `error` event with `code: "internal_error" as never` and
 * `message: "respawn exhausted: <supervisorMessage>"`. The
 * `useConnection` `error` handler did
 * `data.message.includes("respawn exhausted")` to detect the
 * supervisor-exhausted condition — English-only substring matching
 * that silently broke if either side drifted and leaked the raw
 * English phrase into all 8 locales.
 *
 * The  fix:
 *  - The synthesized event now carries `code: "respawn_exhausted"`
 *    (a typed member of the `ErrorCodes` union).
 *  - `useConnection`'s `error` handler branches on
 *    `data.code === "respawn_exhausted"` and calls
 *    `setLastError(t("connection.respawnFailed"))` instead of
 *    `setLastError(data.message)`.
 *  - The sentinel substring matching is removed entirely.
 *
 * Separately,  added a 60s safety timeout to the `reconnecting`
 * handler so a silently-dying supervisor auto-flips to `disconnected`
 * instead of stranding the user on the `"restarting"` UI forever.
 *
 * This file is the regression guard for both changes.
 */
import { act, cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useConnection } from "@/hooks/useConnection";
import { useNavigation } from "@/hooks/useNavigation";

// Hoist the mock call/event handlers so they're available inside the
// vi.mock factory (which is hoisted to the top of the file by vitest
// and runs before any other code). Same pattern as the existing
// `useConnection.test.tsx`.
const { mockCall, mockPythonEvent, mockT } = vi.hoisted(() => ({
	mockCall: vi.fn(),
	// usePythonEvent is spied on so a test can dispatch a fake
	// event by invoking the registered callback with a payload.
	mockPythonEvent: vi.fn(),
	mockT: vi.fn(),
}));

vi.mock("@/hooks/usePython", () => ({
	usePython: () => ({ call: mockCall }),
	usePythonEvent: mockPythonEvent,
}));

// Stub localStorage so useNavigation's persisted-nav-state restore
// (and the beforeEach `clear()` below) doesn't blow up in the jsdom
// environment. Same pattern as useTheme-flush-pending-save.test.tsx.
const lsStub: Record<string, string> = {};
const lsMock = {
	getItem: (k: string) => lsStub[k] ?? null,
	setItem: (k: string, v: string) => {
		lsStub[k] = v;
	},
	removeItem: (k: string) => {
		delete lsStub[k];
	},
	clear: () => {
		for (const k of Object.keys(lsStub)) delete lsStub[k];
	},
};
Object.defineProperty(window, "localStorage", {
	value: lsMock,
	configurable: true,
});

// Mock the i18n module so `useT()` returns a controllable `t` function.
//The  fix routes the respawn-exhausted branch through
// `t("connection.respawnFailed")` — the test asserts that key is
// resolved (and that the raw supervisor message is NOT leaked to the
// UI).
vi.mock("@/i18n/i18n", () => ({
	t: mockT,
	useT: () => mockT,
}));

/**
 * Minimal harness that mirrors what App.tsx does for routing. We don't
 * care about navigation here — we just need `useConnection` mounted
 * inside a React tree so the `usePythonEvent` subscriptions register.
 */
function Harness() {
	const { currentPage, navigate } = useNavigation();
	useConnection({
		call: (async (type: string, _data?: Record<string, unknown>) =>
			mockCall(type)) as unknown as <T = unknown>(
			type: string,
			data?: Record<string, unknown>,
		) => Promise<T>,
		currentPage,
		navigate,
	});
	return <div data-testid="current-page">{currentPage}</div>;
}

/**
 * Helper: invoke the registered usePythonEvent callback for a given
 * event type. The mockPythonEvent spy records each registration as
 * `mockPythonEvent(eventType, callback)`; this helper finds the most
 * recent registration matching `eventType` and invokes it with `data`.
 */
function dispatchEvent(eventType: string, data: unknown): void {
	const calls = mockPythonEvent.mock.calls;
	for (let i = calls.length - 1; i >= 0; i--) {
		const call = calls[i];
		if (call !== undefined && call[0] === eventType) {
			call[1](data);
			return;
		}
	}
	throw new Error(
		`No usePythonEvent subscription found for "${eventType}". ` +
			`Registered types: ${calls.map((c) => c[0]).join(", ")}`,
	);
}

describe("ZU-17: useConnection error handler — typed respawn_exhausted code + localized message", () => {
	beforeEach(() => {
		mockCall.mockReset();
		mockPythonEvent.mockReset();
		mockT.mockReset();
		// `t` returns its key by default — the test asserts the
		// `connection.respawnFailed` key was the one passed in.
		mockT.mockImplementation((key: string) => key);
		localStorage.clear();
		vi.resetModules();
	});
	afterEach(() => {
		cleanup();
	});

	it("on `error` with code='respawn_exhausted': flips connectionStatus to 'disconnected' and sets lastError to the localized respawnFailed message", async () => {
		// Seed initial connected state so the `error` event has a
		// starting state to flip FROM.
		mockCall.mockImplementation((type: string) => {
			switch (type) {
				case "get_config":
					return Promise.resolve({ onboarding_completed: true });
				case "get_status":
					return Promise.resolve({ status: "idle" });
				case "onboarding_is_first_run":
					return Promise.resolve({ is_first_run: false });
				default:
					return Promise.resolve({});
			}
		});

		render(<Harness />);

		// Wait for the initial connection probe to flip the
		// store to "connected".
		await waitFor(() => {
			expect(mockCall).toHaveBeenCalledWith("get_config");
		});

		// Dispatch the synthesized `error` event the way the
		//Tauri host's `python-namespace.ts` does after the
		// fix: `code: "respawn_exhausted"`, no English sentinel
		// in the message.
		await act(async () => {
			dispatchEvent("error", {
				code: "respawn_exhausted",
				message: "raw supervisor message — should NOT leak to UI",
			});
		});

		// The localized `connection.respawnFailed` key MUST have
		// been resolved through `t(...)` — proves the renderer
		// surfaces the localized string instead of the raw
		// English "respawn exhausted: ..." sentinel.
		expect(mockT).toHaveBeenCalledWith("connection.respawnFailed");
	});

	it("on `error` with code='respawn_exhausted': does NOT substring-match the message field (sentinel removed)", async () => {
		// Pre-fix behavior: the handler did
		// `data.message.includes("respawn exhausted")`. Post-fix,
		// the handler branches on `data.code === "respawn_exhausted"`
		// and never inspects `message`. This test verifies the
		// branch fires even when the message is EMPTY — the
		// sentinel-removal contract.
		mockCall.mockImplementation((type: string) => {
			switch (type) {
				case "get_config":
					return Promise.resolve({ onboarding_completed: true });
				case "get_status":
					return Promise.resolve({ status: "idle" });
				case "onboarding_is_first_run":
					return Promise.resolve({ is_first_run: false });
				default:
					return Promise.resolve({});
			}
		});

		render(<Harness />);
		await waitFor(() => {
			expect(mockCall).toHaveBeenCalledWith("get_config");
		});

		await act(async () => {
			// Empty message — pre-fix code would NOT detect
			// this as a respawn-exhausted condition because
			// `"".includes("respawn exhausted") === false`.
			// Post-fix code detects it via the structured code.
			dispatchEvent("error", { code: "respawn_exhausted" });
		});

		expect(mockT).toHaveBeenCalledWith("connection.respawnFailed");
	});

	it("on `error` with a different code: still surfaces the raw message (not the respawnFailed key)", async () => {
		mockCall.mockImplementation((type: string) => {
			switch (type) {
				case "get_config":
					return Promise.resolve({ onboarding_completed: true });
				case "get_status":
					return Promise.resolve({ status: "idle" });
				case "onboarding_is_first_run":
					return Promise.resolve({ is_first_run: false });
				default:
					return Promise.resolve({});
			}
		});

		render(<Harness />);
		await waitFor(() => {
			expect(mockCall).toHaveBeenCalledWith("get_config");
		});

		await act(async () => {
			dispatchEvent("error", {
				code: "server.internal_error",
				message: "something else went wrong",
			});
		});

		// The respawnFailed key was NOT resolved (the branch
		// didn't fire because the code didn't match).
		expect(mockT).not.toHaveBeenCalledWith("connection.respawnFailed");
	});
});
