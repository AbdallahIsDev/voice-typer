/**
 * : detect Linux Wayland without StatusNotifierItem (SNI).
 *
 * Mirrors `voice_typer/server/tray.py::_is_linux_wayland_without_sni` so
 * the Electron main process and the Python tray backend agree on whether
 * a tray icon will be available.
 *
 * When `_tray_unavailable` is true on the Python side (Sway/Hyprland/dwl/
 * river — no SNI watcher on the D-Bus session bus), there is NO tray icon
 * to dismiss the app to. Closing the last window would leave the user
 * with no UI affordance to quit. `index.ts::app.on("window-all-closed")`
 * uses this helper to call `app.quit()` instead of the default no-op on
 * non-macOS when tray is unavailable.
 *
 * Detection (matches the Python contract):
 *   1. `process.platform` starts with "linux".
 *   2. `XDG_SESSION_TYPE=wayland`.
 *   3. The `org.kde.StatusNotifierWatcher` D-Bus name has no owner on the
 *      session bus (probed via `dbus-send` / `gdbus`; if neither tool is
 *      installed, conservatively assume SNI is unavailable — matches the
 *      Python `dbus` module ImportError fallback).
 *
 * The check is synchronous and cached on first call.
 * `index.ts` pre-warms the cache after `startPython()` (so the
 * `window-all-closed` handler returns instantly instead of blocking on
 * the D-Bus subprocess check on the quit hot-path). Subsequent calls
 * return the cached boolean without re-shelling out. The D-Bus subprocess
 * call is ~1ms on a warm session bus.
 * The pre-warm call itself is deferred via `setImmediate` in
 * `index.ts` so the synchronous `execFileSync` probe does NOT block
 * the boot path (`app.whenReady().then(...)` resolution and the
 * dashboard's first `loadURL`/`loadFile` microtask). The cache is
 * still populated long before the user can trigger `window-all-closed`
 * (that requires a rendered dashboard + Python TCP handshake, both
 * multiple event-loop ticks away).
 * R6-F11: `execFileSync` timeout reduced from 2000ms → 500ms. The check
 * is invoked from the `window-all-closed` handler (), which is on the
 * quit hot-path (cache pre-warmed by `index.ts` after `startPython()`).
 * A 2s timeout on a missing `gdbus` /
 * `dbus-send` binary (or a hung D-Bus session) would block the quit
 * sequence for 2s per check — visible "the app takes forever to close"
 * UX bug. 500ms is still 50× the typical warm-cache latency (~1ms) but
 * short enough that a worst-case hang is barely noticeable.
 */
import { execFileSync } from "node:child_process";
import { log } from "./logging";

/** R6-F11: per-call subprocess timeout. Exported for unit tests. */
export const DBUS_PROBE_TIMEOUT_MS = 500;

let _cached: boolean | null = null;

function dbusNameHasOwner(name: string): boolean | null {
	// Try `gdbus` first (GNOME systems), then `dbus-send` (freedesktop.org
	// standard, shipped with dbus-daemon). If neither is on PATH, return
	// null so the caller can apply the conservative fallback.
	const gdbusArgs = [
		"call",
		"--session",
		"--dest",
		"org.freedesktop.DBus",
		"--object-path",
		"/org/freedesktop/DBus",
		"--method",
		"org.freedesktop.DBus.NameHasOwner",
		name,
	];
	try {
		const out = execFileSync("gdbus", gdbusArgs, {
			timeout: DBUS_PROBE_TIMEOUT_MS,
			stdio: ["ignore", "pipe", "pipe"],
		}).toString();
		// gdbus prints `(true,)` or `(false,)` for a boolean return.
		return out.includes("true");
	} catch (e) {
		// gdbus missing / non-zero exit / timeout — fall through to
		// the dbus-send fallback. Logging at warn level (not debug)
		// so a real gdbus install that suddenly starts failing is
		// diagnosable from the runtime log instead of silently
		// degrading to the dbus-send code path.
		//
		// Capture stderr (stdio now `"pipe"` instead of
		// `"ignore"`) so the warn log includes the actual gdbus
		// error message. Without stderr, a "NameHasOwner" D-Bus
		// call that failed with `org.freedesktop.DBus.Error.ServiceUnknown`
		// was logged as a bare `{}` Error object with no message,
		// making production gdbus failures (corrupted install,
		// selinux denial) indistinguishable from a missing-binary
		// ENOENT.
		let stderrText: string | undefined;
		try {
			// `execFileSync` throws on non-zero exit; the captured
			// stderr Buffer is exposed on `e.stderr`.
			const buf = (e as { stderr?: Buffer }).stderr;
			if (buf && buf.length > 0) stderrText = buf.toString().trim();
		} catch {
			// Reading stderr is best-effort; never let it mask
			// the original failure.
		}
		log.warn(
			"[tray_available] gdbus probe failed, falling through to dbus-send:",
			e,
			stderrText ? `stderr: ${stderrText}` : "",
		);
	}
	const dbusSendArgs = [
		"--session",
		"--dest=org.freedesktop.DBus",
		"--type=method_call",
		"--print-reply",
		"/org/freedesktop/DBus",
		"org.freedesktop.DBus.NameHasOwner",
		`string:${name}`,
	];
	try {
		const out = execFileSync("dbus-send", dbusSendArgs, {
			timeout: DBUS_PROBE_TIMEOUT_MS,
			stdio: ["ignore", "pipe", "pipe"],
		}).toString();
		// dbus-send prints `   boolean true` or `   boolean false`.
		return /boolean\s+true/.test(out);
	} catch (e) {
		// Same stderr-capture treatment as the gdbus
		// branch. dbus-send is the final fallback before the
		// conservative "assume SNI unavailable" path, so a debug
		// log (rather than warn) is sufficient — falling through
		// is the expected behavior on systems where dbus-send is
		// not installed (the conservative fallback correctly
		// handles that case).
		let stderrText: string | undefined;
		try {
			const buf = (e as { stderr?: Buffer }).stderr;
			if (buf && buf.length > 0) stderrText = buf.toString().trim();
		} catch {
			// Best-effort.
		}
		log.debug(
			"[tray_available] dbus-send probe failed (conservative fallback will apply):",
			e,
			stderrText ? `stderr: ${stderrText}` : "",
		);
		return null;
	}
}

export function isLinuxWaylandWithoutSni(): boolean {
	if (_cached !== null) return _cached;
	if (!process.platform.startsWith("linux")) {
		_cached = false;
		return false;
	}
	if (process.env.XDG_SESSION_TYPE !== "wayland") {
		_cached = false;
		return false;
	}
	const hasOwner = dbusNameHasOwner("org.kde.StatusNotifierWatcher");
	if (hasOwner === null) {
		// Neither gdbus nor dbus-send available — conservative: assume
		// SNI is NOT available (matches the Python `dbus` ImportError
		// fallback in tray.py:_is_linux_wayland_without_sni).
		_cached = true;
	} else {
		_cached = !hasOwner;
	}
	return _cached;
}

/** Test-only: reset the cached result so unit tests can re-evaluate. */
export function _resetTrayAvailableCache(): void {
	_cached = null;
}

/**
 * Invalidate the cached result so the next `isLinuxWaylandWithoutSni()`
 * call re-probes the D-Bus session bus.
 *
 * The Python sidecar (or any other caller that can prove a tray icon
 * was successfully created on a Wayland compositor that earlier
 * reported no `org.kde.StatusNotifierWatcher` owner) invokes this via
 * the IPC bridge to tell the Electron main process: "your cached
 * `tray_unavailable` answer is stale — re-probe next time."
 *
 * This is the cheaper of the two invalidation strategies: it does NOT
 * shell out to `gdbus` / `dbus-send` here (that would add subprocess
 * overhead to whatever hot-path the caller is on). The next
 * `isLinuxWaylandWithoutSni()` call performs the actual re-probe
 * (~1ms on a warm D-Bus session bus, bounded by
 * `DBUS_PROBE_TIMEOUT_MS`). If the caller is itself on the
 * `window-all-closed` quit hot-path, it should call
 * `isLinuxWaylandWithoutSni()` immediately AFTER this invalidation
 * to re-warm the cache before the next quit decision.
 *
 * Idempotent: calling it when `_cached` is already `null` is a no-op
 * (the next probe will populate the cache).
 */
export function refreshTrayAvailableCache(): void {
	_cached = null;
}
