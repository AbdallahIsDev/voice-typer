/**
 * Tests for useConnectionToasts — covers  (a): connection-state
 * toasts fire with a stable per-transition-type ``id`` so a backend
 * flap REPLACES the existing toast instead of stacking a fresh one.
 *
 * Strategy: mock ``sonner``'s ``toast`` object so each test can assert
 * on the ``id`` field of the options passed to ``toast.error`` /
 * ``toast.warning`` / ``toast.success``. The hook under test is a
 * pure effect — we drive it by re-rendering the harness with a new
 * ``connectionStatus`` prop and asserting the toast calls.
 */
import { act, cleanup, render } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ConnectionStatus } from "@/stores/appStore";

// ── Mocks ────────────────────────────────────────────────────────────
const toastSpies = {
	success: vi.fn(),
	error: vi.fn(),
	warning: vi.fn(),
	info: vi.fn(),
};

vi.mock("sonner", () => ({
	toast: {
		success: toastSpies.success,
		error: toastSpies.error,
		warning: toastSpies.warning,
		info: toastSpies.info,
	},
}));

// Stub the i18n ``t`` so the toast messages are stable strings the
// tests can assert on (the actual translation values are not under
// test here).
vi.mock("@/i18n/i18n", () => ({
	t: (key: string) => `[t]${key}`,
}));

// ── Harness ──────────────────────────────────────────────────────────
// The hook reads connectionStatus via props and fires toasts in an
// effect. We drive it by re-rendering with a new status.
async function renderWithStatus(initial: ConnectionStatus) {
	const { useConnectionToasts } = await import("@/hooks/useConnectionToasts");
	const reloadTheme = vi.fn();
	const t = (key: string) => `[t]${key}`;

	function Probe({ status }: { status: ConnectionStatus }) {
		useConnectionToasts({
			connectionStatus: status,
			reloadThemeFromConfig: reloadTheme,
			t,
		});
		return null as unknown as ReactNode;
	}

	const utils = render(<Probe status={initial} />);
	return {
		...utils,
		reloadTheme,
		rerenderWith: (status: ConnectionStatus) =>
			utils.rerender(<Probe status={status} />),
	};
}

beforeEach(() => {
	for (const k of Object.keys(toastSpies) as (keyof typeof toastSpies)[]) {
		toastSpies[k].mockClear();
	}
});

afterEach(() => {
	cleanup();
});

describe("useConnectionToasts — ZU-33 stable toast ids", () => {
	it("fires toast.error with id='conn-disconnected' on → disconnected", async () => {
		const { rerenderWith } = await renderWithStatus("connecting");
		act(() => {
			rerenderWith("disconnected");
		});
		expect(toastSpies.error).toHaveBeenCalledTimes(1);
		const [msg, opts] = toastSpies.error.mock.calls[0];
		expect(msg).toBe("[t]app.lostConnection");
		expect(opts).toMatchObject({
			id: "conn-disconnected",
			description: "[t]app.lostConnectionHint",
			duration: 6000,
		});
	});

	it("fires toast.warning with id='conn-restarting' on → restarting", async () => {
		const { rerenderWith } = await renderWithStatus("connecting");
		act(() => {
			rerenderWith("restarting");
		});
		expect(toastSpies.warning).toHaveBeenCalledTimes(1);
		const [, opts] = toastSpies.warning.mock.calls[0];
		expect(opts).toMatchObject({
			id: "conn-restarting",
			description: "[t]app.restartingHint",
			duration: 4000,
		});
	});

	it("fires toast.success with id='conn-connected' on RECOVERY (disconnected → connected)", async () => {
		const { rerenderWith } = await renderWithStatus("disconnected");
		act(() => {
			rerenderWith("connected");
		});
		expect(toastSpies.success).toHaveBeenCalledTimes(1);
		const [, opts] = toastSpies.success.mock.calls[0];
		expect(opts).toMatchObject({
			id: "conn-connected",
			duration: 3000,
		});
	});

	it("does NOT fire the connected toast on the INITIAL connecting → connected transition", async () => {
		// Note: the theme-reload DOES fire on the initial connect (the
		// hook reloads theme on every non-connected → connected
		// transition). Only the success TOAST is suppressed — the user
		// just launched the app and doesn't need a "Connected!" toast.
		const { rerenderWith, reloadTheme } = await renderWithStatus("connecting");
		act(() => {
			rerenderWith("connected");
		});
		expect(toastSpies.success).not.toHaveBeenCalled();
		// Theme reload fires regardless (byte-identical to original).
		expect(reloadTheme).toHaveBeenCalledTimes(1);
	});

	it("re-firing disconnected REPLACES the existing disconnected toast (same id)", async () => {
		const { rerenderWith } = await renderWithStatus("connecting");
		// First disconnected transition.
		act(() => {
			rerenderWith("disconnected");
		});
		expect(toastSpies.error).toHaveBeenCalledTimes(1);
		// Backend flaps: briefly restarting, then disconnected again.
		act(() => {
			rerenderWith("restarting");
		});
		act(() => {
			rerenderWith("disconnected");
		});
		// The second disconnected toast uses the SAME id — sonner would
		// replace the existing toast instead of stacking a fresh one.
		expect(toastSpies.error).toHaveBeenCalledTimes(2);
		const firstOpts = toastSpies.error.mock.calls[0][1];
		const secondOpts = toastSpies.error.mock.calls[1][1];
		expect(firstOpts.id).toBe("conn-disconnected");
		expect(secondOpts.id).toBe("conn-disconnected");
		expect(firstOpts.id).toBe(secondOpts.id);
	});

	it("a backend-flap sequence fires each transition exactly once", async () => {
		// connecting → disconnected → restarting → connected → disconnected → restarting → connected
		const { rerenderWith } = await renderWithStatus("connecting");

		act(() => {
			rerenderWith("disconnected");
		});
		act(() => {
			rerenderWith("restarting");
		});
		act(() => {
			rerenderWith("connected");
		});
		act(() => {
			rerenderWith("disconnected");
		});
		act(() => {
			rerenderWith("restarting");
		});
		act(() => {
			rerenderWith("connected");
		});

		// Each transition type fired exactly twice (one per flap cycle).
		expect(toastSpies.error).toHaveBeenCalledTimes(2);
		expect(toastSpies.warning).toHaveBeenCalledTimes(2);
		expect(toastSpies.success).toHaveBeenCalledTimes(2);

		// Every disconnected toast carries the same stable id.
		for (const call of toastSpies.error.mock.calls) {
			expect(call[1].id).toBe("conn-disconnected");
		}
		for (const call of toastSpies.warning.mock.calls) {
			expect(call[1].id).toBe("conn-restarting");
		}
		for (const call of toastSpies.success.mock.calls) {
			expect(call[1].id).toBe("conn-connected");
		}
	});

	it("reloads theme from config when transitioning INTO connected (recovery)", async () => {
		const { rerenderWith, reloadTheme } =
			await renderWithStatus("disconnected");
		act(() => {
			rerenderWith("connected");
		});
		expect(reloadTheme).toHaveBeenCalledTimes(1);
	});

	it("does NOT reload theme on a non-connected transition (disconnected → restarting)", async () => {
		const { rerenderWith, reloadTheme } =
			await renderWithStatus("disconnected");
		act(() => {
			rerenderWith("restarting");
		});
		expect(reloadTheme).not.toHaveBeenCalled();
	});
});
