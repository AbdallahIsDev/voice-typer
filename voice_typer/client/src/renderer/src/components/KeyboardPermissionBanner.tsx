// OS-level keyboard (hotkey) permission banner.
//
// mirrors `MicrophonePermissionBanner` but for the
// keyboard-monitoring permission (macOS Accessibility / Linux `input`
// group + udev rule / Windows: always granted). The renderer probes the
// permission via the existing `onboarding_check_permissions` IPC
// dispatcher on mount and on a 60s refresh interval, and renders an
// AMBER (warning) banner when `state !== "granted"` AND `needed ===
// true`. Reuses the existing dispatcher — no new server-side code
// required (the dispatcher itself wraps
// `voice_typer.server.permissions.check_keyboard_permission()`).
//
// Banner body: "Hotkeys require accessibility permission — click to
// fix" (localized via `useT()` per C-I18N-1).
//
// Click action mirrors `MicrophonePermissionBanner`'s platform-deep-
// link approach:
//   - macOS: `<a href="x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility">`
//     (opens System Settings → Privacy & Security → Accessibility).
//   - Linux: no equivalent standard deep-link, and the renderer cannot
//     directly invoke `pkexec` — the user must run
//     `scripts/linux/install_permissions.py` (or revisit the Onboarding
//     wizard). The banner text tells them what to do; no button is
//     rendered. The backend's `request_keyboard_permission()` helper
//     exists for the native hotkey adapter path (server-side), not the
//     renderer.
//   - Windows: no permission is needed (the dispatcher returns
//     `needed: false`), so the banner never renders.

import { AlertCircleIcon, Settings03Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useEffect, useRef, useState } from "react";
import { usePython } from "@/hooks/usePython";
import { useT } from "@/i18n/i18n";
import type { PermissionsResult } from "@/types/ipc";

// Refresh interval: every 60s. The macOS Accessibility permission can
// be granted at any time in System Settings; the banner should disappear
// within ~1 minute of the user toggling the checkbox without requiring
// a manual refresh. Mirrors the `schedule_permission_retry` cadence
// used server-side (60s, up to 5 attempts).
const KEYBOARD_PERMISSION_REFRESH_MS = 60_000;

/**
 * Internal hook: probes the keyboard permission via
 * `onboarding_check_permissions` on mount + every
 * `KEYBOARD_PERMISSION_REFRESH_MS` ms. Returns the latest
 * {@link PermissionsResult} (or `null` while the first probe is in
 * flight).
 *
 * Mirrors the probe pattern in
 * `pages/onboarding/hooks/usePermissionsProbe.ts` but is intentionally
 * simpler: no test-hotkey listener, no reprobe callback — just a
 * passive periodic read.
 */
function useKeyboardPermission(): PermissionsResult | null {
	const { call } = usePython();
	const [result, setResult] = useState<PermissionsResult | null>(null);

	// Track the in-flight promise + interval id so cleanup is
	// deterministic (mirrors the `cancelled` flag pattern in
	// `usePermissionsProbe` so a setState-after-unmount warning is
	// impossible even if the probe resolves during teardown).
	const cancelledRef = useRef(false);
	const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

	useEffect(() => {
		cancelledRef.current = false;

		const probe = async () => {
			try {
				const r = await call<PermissionsResult>("onboarding_check_permissions");
				if (cancelledRef.current) return;
				setResult(r);
			} catch (err) {
				if (cancelledRef.current) return;
				// Probe failed — surface as an "error"
				// state so the banner renders (better to
				// nag the user than to silently hide a
				// real permission problem). Mirrors the
				// onboarding probe's error-handling shape.
				console.error("[renderer:KeyboardPermissionBanner] probe failed:", err);
				setResult({
					platform: "unknown",
					state: "error",
					needed: true,
					instructions: null,
				});
			}
		};

		void probe();

		intervalRef.current = setInterval(() => {
			void probe();
		}, KEYBOARD_PERMISSION_REFRESH_MS);

		return () => {
			cancelledRef.current = true;
			if (intervalRef.current !== null) {
				clearInterval(intervalRef.current);
				intervalRef.current = null;
			}
		};
	}, [call]);

	return result;
}

export interface KeyboardPermissionBannerProps {
	/**
	 * Override the permission result (used by tests to inject a
	 * fixed state without mocking `usePython`). When omitted, the
	 * component probes via {@link useKeyboardPermission} on mount +
	 * every 60s.
	 */
	permissionResult?: PermissionsResult | null;
}

/**
 * Renders an amber "Hotkeys require accessibility permission — click
 * to fix" banner when the OS has NOT granted the keyboard-monitoring
 * permission (macOS Accessibility / Linux input group + udev rule).
 *
 * Returns `null` when:
 *   - `result` is `null` (first probe still in flight) — avoid a
 *     flash-of-banner on every page mount.
 *   - `result.state === "granted"` — permission is fine.
 *   - `result.needed === false` — platform doesn't require the
 *     permission (Windows, unknown) — no banner to show.
 *
 * Click-through: opens the OS privacy deep-link on macOS; on Linux /
 * Windows the banner body still renders (telling the user what to do)
 * but no deep-link button is shown, mirroring
 * `MicrophonePermissionBanner`'s platform branching.
 */
export function KeyboardPermissionBanner({
	permissionResult,
}: KeyboardPermissionBannerProps) {
	const t = useT();
	const probed = useKeyboardPermission();
	const result = permissionResult ?? probed;

	// First probe still in flight — don't flash a banner. Also guard
	// `undefined`: a stub/mocked `onboarding_check_permissions` bridge
	// can resolve to `undefined`, and `permissionResult ?? probed`
	// would then pass it straight through to `result.state` below
	// (crash). Treat both as "no result yet".
	if (result === null || result === undefined) return null;
	// Permission granted or not needed — no banner.
	if (result.state === "granted") return null;
	if (result.needed === false) return null;

	const ua =
		typeof navigator !== "undefined" ? navigator.userAgent.toLowerCase() : "";

	// macOS exposes a deep-link URL scheme to the OS Accessibility
	// settings; Linux has no equivalent standard; Windows never
	// renders the banner (needed === false).
	let deepLink: string | null = null;
	if (ua.includes("mac")) {
		deepLink =
			"x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility";
	}

	return (
		<div
			role="alert"
			className="rounded-lg border border-warning/40 bg-warning/10 p-4 space-y-2"
		>
			<div className="flex items-start gap-2">
				<HugeiconsIcon
					icon={AlertCircleIcon}
					strokeWidth={1.625}
					className="h-4 w-4 shrink-0 mt-0.5 text-warning"
				/>
				<div className="flex-1 space-y-1">
					<p className="text-sm font-semibold text-warning">
						{t("keyboard.permissionDeniedTitle")}
					</p>
					<p className="text-xs text-(--text-primary)">
						{t("keyboard.permissionDeniedMessage")}
					</p>
				</div>
			</div>
			{deepLink && (
				<a
					href={deepLink}
					aria-label={t("keyboard.openSettingsAria")}
					className="inline-flex items-center gap-1.5 rounded-md border border-warning/40 bg-warning/5 px-3 py-1.5 text-xs font-medium text-warning hover:bg-warning/10 transition-colors"
				>
					<HugeiconsIcon
						icon={Settings03Icon}
						strokeWidth={1.625}
						className="h-3.5 w-3.5"
					/>
					{t("keyboard.openSettings")}
				</a>
			)}
		</div>
	);
}
