/**
 * Tests for `useConnection` `handleRetryConnection` — the OPTION-A
 * escalation: probe first, and when the probe fails, ask the Electron
 * main process to restart ONLY the Python backend (`backend:restart`
 * channel via `window.window_.restartBackend`).
 *
 * Status flow under test:
 *   - probe ok                          → "connected" (no restart call)
 *   - probe fail + restart accepted     → "restarting" (fresh backend's
 *     `state_changed` push flips back to "connected" — exercised by the
 *     existing state_changed tests)
 *   - probe fail + restart declined     → "disconnected" + lastError
 *     (adopted mode / relaunch in-flight / bridge missing)
 */
import {
	cleanup,
	fireEvent,
	render,
	screen,
	waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Shared stable-mocks preamble (see helpers/stableMocks.tsx): the
// assertable singletons + the usePython mock factory binding.
import { pythonMock, resetStableMocks, stableMocks } from "@/__tests__/helpers/stableMocks";

const { mockCall } = stableMocks;

vi.mock("@/hooks/usePython", () => pythonMock());

import { useConnection } from "@/hooks/useConnection";
import { useAppStore } from "@/stores/appStore";

// Stub localStorage (jsdom 29 with opaque origin doesn't expose it).
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

interface BridgeLike {
	restartBackend?: () => Promise<{ ok: boolean; reason?: string }>;
}

/**
 * Minimal harness: mounts the hook and exposes the retry button +
 * current status/lastError so tests can drive the escalation.
 */
function Harness() {
	const { handleRetryConnection } = useConnection({
		call: (async (type: string) => mockCall(type)) as unknown as <T = unknown>(
			type: string,
			data?: Record<string, unknown>,
		) => Promise<T>,
		navigate: () => {},
	});
	const connectionStatus = useAppStore((s) => s.connectionStatus);
	const lastError = useAppStore((s) => s.lastError);

	// Press the same "Retry" button ConnectionStatusScreen renders.
	return (
		<>
			<span data-testid="status">{connectionStatus}</span>
			{lastError ? <span data-testid="last-error">{lastError}</span> : null}
			<button type="button" onClick={() => void handleRetryConnection()}>
				Retry
			</button>
		</>
	);
}

const CONFIG_OK = { onboarding_completed: true };

describe("useConnection — OPTION-A retry escalation (probe → backend restart)", () => {
	beforeEach(() => {
		resetStableMocks();
		useAppStore.getState().setConnectionStatus("connecting");
		useAppStore.getState().setLastError(null);
		// First mount-probe succeeds (backend healthy); subsequent
		// calls reject (backend dies) — per-test overrides adjust this.
		let getConfigCalls = 0;
		mockCall.mockImplementation((type: string) => {
			switch (type) {
				case "get_config":
					getConfigCalls++;
					if (getConfigCalls > 1) {
						return Promise.reject(new Error("down"));
					}
					return Promise.resolve(CONFIG_OK);
				case "get_status":
					return Promise.resolve({ status: "idle" });
				case "onboarding_is_first_run":
					return Promise.resolve({ is_first_run: false });
				default:
					return Promise.resolve({});
			}
		});
	});

	afterEach(() => {
		cleanup();
		delete (window as unknown as Record<string, unknown>).window_;
	});

	it("probe ok → connected, without calling restartBackend", async () => {
		const restartBackend = vi.fn();
		(window as unknown as { window_: BridgeLike }).window_ = {
			restartBackend,
		};
		// Backend healthy the whole time — every get_config resolves.
		mockCall.mockImplementation((type: string) => {
			switch (type) {
				case "get_config":
					return Promise.resolve(CONFIG_OK);
				case "get_status":
					return Promise.resolve({ status: "idle" });
				case "onboarding_is_first_run":
					return Promise.resolve({ is_first_run: false });
				default:
					return Promise.resolve({});
			}
		});
		render(<Harness />);

		// First probe (mount) succeeds → connected.
		await waitFor(() => {
			expect(screen.getByTestId("status").textContent).toBe("connected");
		});
		// Click Retry with a backend that answers the probe.
		fireEvent.click(screen.getByText("Retry"));
		await waitFor(() => {
			expect(screen.getByTestId("status").textContent).toBe("connected");
		});
		expect(restartBackend).not.toHaveBeenCalled();
	});

	it("probe fail + restart accepted → restarting", async () => {
		const restartBackend = vi.fn().mockResolvedValue({ ok: true });
		(window as unknown as Record<string, unknown>).window_ = {
			restartBackend,
		};
		render(<Harness />);

		// Backend answers the mount probe, then dies.
		await waitFor(() => {
			expect(screen.getByTestId("status").textContent).toBe("connected");
		});

		fireEvent.click(screen.getByText("Retry"));
		await waitFor(() => {
			expect(screen.getByTestId("status").textContent).toBe("restarting");
		});
		expect(restartBackend).toHaveBeenCalledTimes(1);
	});

	it("probe fail + restart declined (adopted/relaunching) → disconnected + hint", async () => {
		const restartBackend = vi
			.fn()
			.mockResolvedValue({ ok: false, reason: "adopted" });
		(window as unknown as Record<string, unknown>).window_ = {
			restartBackend,
		};
		render(<Harness />);

		await waitFor(() => {
			expect(screen.getByTestId("status").textContent).toBe("connected");
		});

		fireEvent.click(screen.getByText("Retry"));
		await waitFor(() => {
			expect(screen.getByTestId("status").textContent).toBe("disconnected");
		});
		expect(restartBackend).toHaveBeenCalledTimes(1);
		// User-visible hint that single-click recovery was attempted
		// but the backend is parent-managed.
		expect(screen.getByTestId("last-error").textContent).toContain(
			"could not be restarted",
		);
	});

	it("probe fail + bridge missing (tauri/old preload) → disconnected + hint, no crash", async () => {
		// No window_.restartBackend — optional chaining must degrade
		// to the bare-probe behavior.
		delete (window as unknown as Record<string, unknown>).window_;
		render(<Harness />);

		await waitFor(() => {
			expect(screen.getByTestId("status").textContent).toBe("connected");
		});

		fireEvent.click(screen.getByText("Retry"));
		await waitFor(() => {
			expect(screen.getByTestId("status").textContent).toBe("disconnected");
		});
		expect(screen.getByTestId("last-error").textContent).toContain(
			"could not be restarted",
		);
	});
});
