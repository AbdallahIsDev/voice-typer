/**
 * Tests for useSnackbar — covers  (action option on ShowSnackOptions
 * + the showRetryableToast helper).
 *
 * Strategy: mock the ``sonner`` module so we can capture every call to
 * ``toast.success`` / ``toast.error`` / ``toast.warning`` / ``toast.info``
 * and assert the options object carries the expected ``action`` and
 * ``duration``. The hook under test is a thin delegator — what we want
 * to verify is the SHAPE of the options it forwards, not the rendering
 * of the toast (sonner has its own test coverage for rendering).
 */
import { act, cleanup, render } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// ── Mocks ────────────────────────────────────────────────────────────
// Capture every call to the sonner toast methods so the tests can
// assert on the forwarded message + options object.
const toastSpies = {
	success: vi.fn(),
	error: vi.fn(),
	warning: vi.fn(),
	info: vi.fn(),
	dismiss: vi.fn(),
};

vi.mock("sonner", () => ({
	toast: {
		success: toastSpies.success,
		error: toastSpies.error,
		warning: toastSpies.warning,
		info: toastSpies.info,
		dismiss: toastSpies.dismiss,
	},
}));

// Stub the i18n ``t`` so we don't need to wire the full locale catalog.
// ``t("common.undo")`` returns a stable string the test can assert on.
vi.mock("@/i18n/i18n", () => ({
	t: (key: string) =>
		key === "common.undo" ? "Undo" : key === "common.retry" ? "Retry" : key,
}));

// ── Harness ──────────────────────────────────────────────────────────
async function renderWithHook() {
	const { useSnackbar } = await import("@/hooks/useSnackbar");
	const captures: ReturnType<typeof useSnackbar> = {
		showSnack: () => {},
		clearSnack: () => {},
	};
	function Probe() {
		Object.assign(captures, useSnackbar());
		return null as unknown as ReactNode;
	}
	render(<Probe />);
	return captures;
}

beforeEach(() => {
	for (const k of Object.keys(toastSpies) as (keyof typeof toastSpies)[]) {
		toastSpies[k].mockClear();
	}
});

afterEach(() => {
	cleanup();
});

describe("useSnackbar — ZU-33 action option", () => {
	it("forwards the action option to sonner.toast.error", async () => {
		const { showSnack } = await renderWithHook();
		const onClick = vi.fn();
		act(() => {
			showSnack("save failed", "error", {
				action: { label: "Retry", onClick },
			});
		});
		expect(toastSpies.error).toHaveBeenCalledTimes(1);
		const [msg, opts] = toastSpies.error.mock.calls[0] ?? [];
		expect(msg).toBe("save failed");
		expect(opts).toMatchObject({
			duration: 8000, // per-type default for error
			action: { label: "Retry", onClick },
		});
	});

	it("forwards the action option to toast.success", async () => {
		const { showSnack } = await renderWithHook();
		const onClick = vi.fn();
		act(() => {
			showSnack("copied", "success", {
				action: { label: "View", onClick },
			});
		});
		expect(toastSpies.success).toHaveBeenCalledTimes(1);
		const [, opts] = toastSpies.success.mock.calls[0] ?? [];
		expect(opts).toMatchObject({
			duration: 3000,
			action: { label: "View", onClick },
		});
	});

	it("forwards the id option alongside the action (dedup-by-id)", async () => {
		const { showSnack } = await renderWithHook();
		const onClick = vi.fn();
		act(() => {
			showSnack("still saving", "warning", {
				id: "save-in-progress",
				action: { label: "Cancel", onClick },
			});
		});
		expect(toastSpies.warning).toHaveBeenCalledTimes(1);
		const [, opts] = toastSpies.warning.mock.calls[0] ?? [];
		expect(opts).toMatchObject({
			id: "save-in-progress",
			duration: 6000,
			action: { label: "Cancel", onClick },
		});
	});

	it("forwards the description option alongside the message", async () => {
		const { showSnack } = await renderWithHook();
		act(() => {
			showSnack("backend lost", "warning", {
				id: "conn-disconnected",
				description: "Reconnect the microphone",
			});
		});
		expect(toastSpies.warning).toHaveBeenCalledTimes(1);
		const [msg, opts] = toastSpies.warning.mock.calls[0] ?? [];
		expect(msg).toBe("backend lost");
		expect(opts).toMatchObject({
			id: "conn-disconnected",
			description: "Reconnect the microphone",
			duration: 6000,
		});
	});

	it("omits action and id from the forwarded opts when not provided", async () => {
		const { showSnack } = await renderWithHook();
		act(() => {
			showSnack("hello", "info");
		});
		expect(toastSpies.info).toHaveBeenCalledTimes(1);
		const [, opts] = toastSpies.info.mock.calls[0] ?? [];
		// duration is always forwarded; action/id must NOT be present.
		expect(opts).not.toHaveProperty("action");
		expect(opts).not.toHaveProperty("id");
		expect(opts).toHaveProperty("duration", 4000);
	});

	it("clearSnack(id) delegates to toast.dismiss(id)", async () => {
		const { clearSnack } = await renderWithHook();
		act(() => {
			clearSnack("my-toast-id");
		});
		expect(toastSpies.dismiss).toHaveBeenCalledWith("my-toast-id");
	});

	it("clearSnack() with no id dismisses all toasts", async () => {
		const { clearSnack } = await renderWithHook();
		act(() => {
			clearSnack();
		});
		expect(toastSpies.dismiss).toHaveBeenCalledWith();
	});
});

describe("showRetryableToast — ZU-33 helper", () => {
	it("renders an error toast with a Retry action button by default", async () => {
		const { showRetryableToast } = await import("@/hooks/useSnackbar");
		const onRetry = vi.fn();
		act(() => {
			showRetryableToast("save failed", onRetry);
		});
		expect(toastSpies.error).toHaveBeenCalledTimes(1);
		const [msg, opts] = toastSpies.error.mock.calls[0] ?? [];
		expect(msg).toBe("save failed");
		// Default type is "error" → per-type default duration = 8000ms.
		expect(opts).toMatchObject({
			duration: 8000,
			action: { label: "Retry", onClick: onRetry },
		});
	});

	it("respects an explicit retryLabel override", async () => {
		const { showRetryableToast } = await import("@/hooks/useSnackbar");
		const onRetry = vi.fn();
		act(() => {
			showRetryableToast("export failed", onRetry, {
				retryLabel: "Try Again",
			});
		});
		expect(toastSpies.error).toHaveBeenCalledTimes(1);
		const [, opts] = toastSpies.error.mock.calls[0] ?? [];
		expect(opts.action).toEqual({ label: "Try Again", onClick: onRetry });
	});

	it("respects an explicit type override (warning)", async () => {
		const { showRetryableToast } = await import("@/hooks/useSnackbar");
		const onRetry = vi.fn();
		act(() => {
			showRetryableToast("mic test warning", onRetry, { type: "warning" });
		});
		expect(toastSpies.warning).toHaveBeenCalledTimes(1);
		expect(toastSpies.error).not.toHaveBeenCalled();
		const [, opts] = toastSpies.warning.mock.calls[0] ?? [];
		// warning per-type default duration = 6000ms.
		expect(opts).toMatchObject({
			duration: 6000,
			action: { label: "Retry", onClick: onRetry },
		});
	});

	it("respects an explicit timeoutMs override", async () => {
		const { showRetryableToast } = await import("@/hooks/useSnackbar");
		const onRetry = vi.fn();
		act(() => {
			showRetryableToast("download failed", onRetry, { timeoutMs: 12000 });
		});
		expect(toastSpies.error).toHaveBeenCalledTimes(1);
		const [, opts] = toastSpies.error.mock.calls[0] ?? [];
		expect(opts).toHaveProperty("duration", 12000);
	});

	it("mirrors showUndoableToast structure (sanity check)", async () => {
		const { showUndoableToast } = await import("@/hooks/useSnackbar");
		const onUndo = vi.fn();
		act(() => {
			showUndoableToast("deleted", onUndo);
		});
		expect(toastSpies.warning).toHaveBeenCalledTimes(1);
		const [, opts] = toastSpies.warning.mock.calls[0] ?? [];
		// showUndoableToast defaults to type="warning", undoLabel=t("common.undo")="Undo".
		expect(opts).toMatchObject({
			duration: 6000,
			action: { label: "Undo", onClick: onUndo },
		});
	});
});
