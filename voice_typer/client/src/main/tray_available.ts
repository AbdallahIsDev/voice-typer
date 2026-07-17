/**
 * CR-20: detect Linux Wayland without StatusNotifierItem (SNI).
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
 * The check is synchronous and runs once at module load (the same
 * contract as the Python side, which runs the check inside `__init__`
 * before the tray thread starts). The D-Bus subprocess call is ~1ms on
 * a warm session bus.
 */
import { execFileSync } from "node:child_process";

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
			timeout: 2000,
			stdio: ["ignore", "pipe", "ignore"],
		}).toString();
		// gdbus prints `(true,)` or `(false,)` for a boolean return.
		return out.includes("true");
	} catch {
		// fall through to dbus-send
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
			timeout: 2000,
			stdio: ["ignore", "pipe", "ignore"],
		}).toString();
		// dbus-send prints `   boolean true` or `   boolean false`.
		return /boolean\s+true/.test(out);
	} catch {
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
