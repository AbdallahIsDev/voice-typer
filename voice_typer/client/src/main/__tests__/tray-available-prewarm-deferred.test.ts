// @vitest-environment node
/**
 * Unit tests for the deferred pre-warm of `isLinuxWaylandWithoutSni()`
 * in `index.ts`.
 *
 * Background: the D-Bus probe (`execFileSync("gdbus", …)` /
 * `execFileSync("dbus-send", …)`) inside `tray_available.ts` is
 * synchronous. Previously `index.ts` called `isLinuxWaylandWithoutSni()`
 * directly inside the `app.whenReady().then(...)` callback, which ran
 * the probe on the same event-loop tick as `startPython()` and the
 * dashboard's `loadURL`/`loadFile` kickoff — blocking the boot path
 * on Linux Wayland for up to ~1ms (warm) or up to 500ms (worst-case
 * timeout per probe × 2 probes).
 *
 * The fix defers the pre-warm via `setImmediate(...)` so the probe
 * runs on the next event-loop tick, AFTER the dashboard BrowserWindow's
 * `loadURL` Promise has had its first microtask turn.
 *
 * These tests verify the deferral by inspecting `index.ts` source text.
 * Importing `index.ts` directly would fire Electron APIs at module-eval
 * time (single-instance lock, security-warning suppression, etc.) and
 * is not testable in vitest without mocking the entire Electron
 * runtime — the project's convention (see `main-process-fixes.test.ts`,
 * `window-open-logs.test.ts`) for `index.ts` structural assertions is
 * source-text inspection via `fs.readFileSync`.
 */
import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

function readIndexSrc(): string {
	return fs.readFileSync(path.resolve(__dirname, "../index.ts"), "utf-8");
}

/**
 * Extract the pre-warm block — the source-text region between the
 * `// pre-warm the Wayland-without-SNI cache` comment and the closing
 * `});` of the `app.whenReady().then(...)` callback that follows it.
 *
 * Anchoring on the comment (rather than on `app.whenReady()`) keeps
 * the slice small and avoids accidentally matching unrelated
 * `setImmediate` / `isLinuxWaylandWithoutSni` references elsewhere in
 * the file. The comment block + the `setImmediate(...)` body together
 * fit comfortably in a 3000-char window.
 */
function readPrewarmBlock(): string {
	const src = readIndexSrc();
	const start = src.indexOf("pre-warm the Wayland-without-SNI cache");
	expect(start).toBeGreaterThan(-1);
	return src.slice(start, start + 3000);
}

describe("tray_available pre-warm: deferred via setImmediate (not synchronous)", () => {
	const src = readIndexSrc();

	it("index.ts still imports isLinuxWaylandWithoutSni from ./tray_available", () => {
		// Sanity: the import has not been removed or renamed.
		expect(src).toMatch(
			/import\s+\{\s*isLinuxWaylandWithoutSni\s*\}\s+from\s+["']\.\/tray_available["']/,
		);
	});

	it("index.ts contains a setImmediate(...) call inside app.whenReady().then(...)", () => {
		// Anchor on the whenReady callback so we only inspect
		// the boot path, not unrelated code (e.g. the
		// will-quit handler also schedules things).
		const whenReadyIdx = src.search(/app\.whenReady\(\)\.then\(/);
		expect(whenReadyIdx).toBeGreaterThan(-1);
		// Slice a generous window covering the entire
		// whenReady callback body (the handler ends at the
		// matching `});` after the pre-warm block). The
		// pre-warm comment + setImmediate body live ~5KB
		// into the whenReady body (the comment alone is
		// ~2KB), so a 7000-char window covers everything.
		const block = src.slice(whenReadyIdx, whenReadyIdx + 7000);
		expect(block).toMatch(/setImmediate\s*\(/);
	});

	it("isLinuxWaylandWithoutSni() is invoked INSIDE a setImmediate callback, NOT as a bare statement", () => {
		const block = readPrewarmBlock();

		// The setImmediate call must wrap the
		// isLinuxWaylandWithoutSni() invocation — assert
		// there is a setImmediate whose body contains the
		// call.
		const setImmediateIdx = block.indexOf("setImmediate(() => {");
		expect(setImmediateIdx).toBeGreaterThan(-1);
		const afterSetImmediate = block.slice(setImmediateIdx);
		// Within ~300 chars of the setImmediate, the
		// isLinuxWaylandWithoutSni() call must appear (it
		// is wrapped in try/catch, so it is indented inside
		// the setImmediate callback body).
		const callIdx = afterSetImmediate.indexOf("isLinuxWaylandWithoutSni()");
		expect(callIdx).toBeGreaterThan(-1);
		expect(callIdx).toBeLessThan(400);
	});

	it("the deferred pre-warm is wrapped in try/catch (uncaught exceptions on the next tick would crash)", () => {
		// A throw inside a setImmediate callback surfaces as
		// an uncaught exception — not a rejected Promise.
		// The production code MUST swallow + log probe
		// failures so a transient D-Bus hiccup on the boot
		// path does not crash Electron. (The probe itself
		// already swallows execFileSync errors internally,
		// but the try/catch is belt-and-suspenders for any
		// future throw path inside isLinuxWaylandWithoutSni
		// or the cache reset.)
		const block = readPrewarmBlock();
		const setImmediateIdx = block.indexOf("setImmediate(() => {");
		expect(setImmediateIdx).toBeGreaterThan(-1);
		// Slice the setImmediate body (up to the matching
		// `});` that closes it).
		const setImmediateBlock = block.slice(setImmediateIdx);
		const tryIdx = setImmediateBlock.search(/try\s*\{/);
		expect(tryIdx).toBeGreaterThan(-1);
		const catchIdx = setImmediateBlock.search(/}\s*catch\s*\(/);
		expect(catchIdx).toBeGreaterThan(-1);
		expect(catchIdx).toBeGreaterThan(tryIdx);
		// The catch must log (not silently swallow).
		const catchBlock = setImmediateBlock.slice(catchIdx);
		expect(catchBlock).toMatch(/log\.warn/);
	});

	it("isLinuxWaylandWithoutSni() is NOT called as a bare synchronous statement right after startPython()", () => {
		// The old (pre-fix) code wrote `isLinuxWaylandWithoutSni();`
		// as a bare statement immediately after `startPython();`.
		// The fix moves it inside setImmediate. Assert there is no
		// bare `isLinuxWaylandWithoutSni();` statement appearing
		// OUTSIDE the setImmediate callback.
		//
		// Strategy: split the pre-warm block into lines and
		// verify that every `isLinuxWaylandWithoutSni();`
		// invocation is nested inside the setImmediate
		// callback body (brace depth > 0 from the
		// setImmediate(() => { opener).
		const block = readPrewarmBlock();
		const lines = block.split(/\n/);
		let depth = 0;
		let sawSetImmediate = false;
		let bareCallOutsideSetImmediate = false;
		for (const line of lines) {
			if (/setImmediate\s*\(\s*\(\)\s*=>\s*\{/.test(line)) {
				sawSetImmediate = true;
				depth += 1;
				continue;
			}
			// Crude brace-depth tracker: counts { and }
			// outside strings. Sufficient for the
			// well-formatted, linted index.ts source.
			const opens = (line.match(/\{/g) ?? []).length;
			const closes = (line.match(/\}/g) ?? []).length;
			// If this line is the bare call
			// `isLinuxWaylandWithoutSni();` and we are
			// NOT inside the setImmediate callback body
			// (depth <= 0 from setImmediate), it is a
			// regression.
			if (/^\s*isLinuxWaylandWithoutSni\(\);/.test(line)) {
				if (depth <= 0) {
					bareCallOutsideSetImmediate = true;
					break;
				}
			}
			depth += opens - closes;
			if (depth < 0) depth = 0;
		}
		expect(sawSetImmediate).toBe(true);
		expect(bareCallOutsideSetImmediate).toBe(false);
	});
});
