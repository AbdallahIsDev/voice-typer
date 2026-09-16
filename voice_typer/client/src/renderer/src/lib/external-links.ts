// lib/external-links.ts
//
// MO-118: the ONE helper every external https link routes through.
//
// Under Electron, bare `<a target="_blank">` anchors and `window.open`
// calls are intercepted by `setWindowOpenHandler` +
// `will-navigate`(`input-nav-guard.ts`), which `shell.openExternal`s the
// https URLs and denies the rest. The Tauri webview has no such host-side
// interception: with the CSP at `default-src 'self'` and
// `plugins.shell.open = false` (C-TAURI-2), a bare `window.open` is
// either blocked or traps the target page inside the app, leaving every
// help / feedback / changelog / share link dead.
//
// The fix routes every call site through `window.window_.openExternalUrl`
// (`open_external_url_command` in Rust), which enforces the SAME
// https-only policy as `input-nav-guard.ts` and opens the URL with the
// OS-default browser (`explorer.exe` / `open` / `xdg-open`).
//
// The helper still falls back to the classic browser behavior when the
// bridge is absent (tests, bubble runtime, or a future runtime that
// omits the namespace): the anchor default / `window.open` is the only
// thing available there, and it is exactly what Electron's guard used to
// allow anyway for https.

/** Result envelope, mirrors the other `window.window_` helpers. */
type OpenExternalResult = { success: boolean; error?: string };

/**
 * Open an https URL outside the app (default browser).
 *
 * - Tauri (bridge present): invokes the Rust command; the host opens the
 *   URL with the OS handler after its https-only check.
 * - Bridge absent (tests / non-main windows): falls back to
 *   `window.open(url, "_blank", "noopener,noreferrer")`.
 *
 * Returns `{success: true}` when the open was dispatched successfully,
 * `{success: false, error}` otherwise. Never throws.
 */
export async function openExternalUrl(
	url: string,
): Promise<OpenExternalResult> {
	const opener = window.window_?.openExternalUrl;
	if (typeof opener === "function") {
		try {
			return await opener(url);
		} catch (e) {
			return {
				success: false,
				error: e instanceof Error ? e.message : String(e),
			};
		}
	}
	// Fallback path (bridge absent): the classic web behavior.
	try {
		const opened = window.open(url, "_blank", "noopener,noreferrer");
		return opened
			? { success: true }
			: { success: false, error: "popup-blocked" };
	} catch (e) {
		return {
			success: false,
			error: e instanceof Error ? e.message : String(e),
		};
	}
}
