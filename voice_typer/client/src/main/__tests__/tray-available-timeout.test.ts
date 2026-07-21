// @vitest-environment node
/**
 * R6-F11 unit tests for `tray_available.ts`.
 *
 * Verifies that:
 *   - The exported `DBUS_PROBE_TIMEOUT_MS` constant is 500 (was 2000).
 *   - `execFileSync` is invoked with `timeout: 500` (not 2000).
 *
 * We mock `node:child_process.execFileSync` so the test doesn't
 * actually shell out, and we capture the options passed to it.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

// Capture execFileSync invocations.
// Signature mirrors execFileSync(file, args?, options?) — typed as a tuple
// of [string, string[], { timeout?: number; ... }] so we can index [2]
// for the options bag in the assertion below.
const mockExecFileSync = vi.fn(
	(_file: string, _args?: string[], _options?: { timeout?: number }): Buffer =>
		Buffer.from("(false,)"),
);
vi.mock("node:child_process", () => ({
	execFileSync: mockExecFileSync,
}));

describe("R6-F11: tray_available.ts reduced execFileSync timeout", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		vi.resetModules();
	});

	it("exports DBUS_PROBE_TIMEOUT_MS = 500", async () => {
		const mod = await import("../tray_available");
		expect(mod.DBUS_PROBE_TIMEOUT_MS).toBe(500);
	});

	it("passes timeout: 500 to execFileSync (NOT 2000)", async () => {
		// Force Linux + Wayland so the function actually probes.
		const originalPlatform = process.platform;
		const originalSession = process.env.XDG_SESSION_TYPE;
		try {
			Object.defineProperty(process, "platform", {
				value: "linux",
				configurable: true,
			});
			process.env.XDG_SESSION_TYPE = "wayland";

			const mod = await import("../tray_available");
			mod._resetTrayAvailableCache();
			mod.isLinuxWaylandWithoutSni();

			expect(mockExecFileSync).toHaveBeenCalled();
			for (const call of mockExecFileSync.mock.calls) {
				const opts = call[2] as { timeout?: number } | undefined;
				expect(opts?.timeout).toBe(500);
				// Sanity: it MUST NOT be the old 2000ms value.
				expect(opts?.timeout).not.toBe(2000);
			}
		} finally {
			Object.defineProperty(process, "platform", {
				value: originalPlatform,
				configurable: true,
			});
			if (originalSession === undefined) {
				delete process.env.XDG_SESSION_TYPE;
			} else {
				process.env.XDG_SESSION_TYPE = originalSession;
			}
		}
	});
});
