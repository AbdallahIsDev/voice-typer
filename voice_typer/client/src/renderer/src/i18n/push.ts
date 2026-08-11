// Cross-boundary IPC push helpers + tray-label resolver.
//
// ``setLocale`` (in ``./store``) calls these pushers after the locale
// mutates so the Electron main process and the Python sidecar both
// learn about the locale switch and can localise their native surfaces
// (main-process dialogs, system-tray menu items).
//
// Both pushers are best-effort: the bridge surfaces
// (``window.window_`` / ``window.python``) may be missing during
// module-init or under Tauri, so they swallow sync throws and promise
// rejections via ``console.warn`` — a locale-switch failure must never
// break the UI.
//
// ``trayLabelsForLocale`` uses ``t`` (from ``./translate``) to resolve
// the renderer-known tray-menu label keys against the current locale,
// falling back to English per the standard ``t`` lookup path.

import type { Locale } from "./locale";
import { t } from "./translate";

/**
 * : build a dictionary of tray-menu label keys → localized strings
 * for the current locale. Keys whose translation resolves to the raw key
 * itself (meaning the key is missing from both the current locale and
 * English) are excluded so the backend keeps its English defaults.
 *
 * The returned object is sent to the Python sidecar via
 * ``window.python.call({type: "set_tray_locale", data: {locale, labels}})``
 * so tray-menu items localise without a backend restart.
 */
export function trayLabelsForLocale(): Record<string, string> {
	const labels: Record<string, string> = {};
	const entries: [string, string][] = [
		["models", "models.title"],
		["microphones", "microphone.microphone"],
		// HU-17: push the server-notification labels too so the Python
		// sidecar's tray notifications (config-load failure, state
		// changes) follow the renderer's locale instead of staying
		// English. Same dotted-key lookup as the menu labels — an entry
		// is skipped when the current locale lacks a translation.
		["error.config_load_failed.title", "error.config_load_failed.title"],
		["error.config_load_failed.body", "error.config_load_failed.body"],
		["state.app.starting", "state.app.starting"],
	];
	for (const [key, labelKey] of entries) {
		const value = t(labelKey);
		// Skip entries where the translation equals the raw key —
		// the key is missing from both the current locale and
		// English, so the backend should keep its default.
		if (value !== labelKey) {
			labels[key] = value;
		}
	}
	return labels;
}

/**
 * Best-effort push of the current locale to the Electron main process
 * via the ``window.window_.setLocale(locale)`` IPC bridge (registered
 * in ``main/ipc/window-handlers.ts`` as the ``i18n:set-locale``
 * handler). The main process uses the pushed locale to localise native
 * dialogs (single-instance error, critical-error dialog, model-folder
 * picker, export save-as dialogs).
 *
 * No-op when the bridge is missing (Tauri host, module-init scenario
 * where the preload bridge isn't installed yet). Rejections and sync
 * throws are caught and logged via ``console.warn`` so a locale switch
 * never crashes the renderer.
 */
export function pushLocaleToMainProcess(locale: Locale): void {
	try {
		// Read directly from the globally-augmented ``window.window_``
		// (declared in ``types/ipc/bubble_bridge.ts``) instead of
		// re-declaring the bridge shape inline via an
		// ``as unknown as { window_?: ... }`` cast. The cast was
		// structurally identical but duplicated the type contract.
		const result = window.window_?.setLocale?.(locale);
		if (result && typeof (result as Promise<unknown>).then === "function") {
			(result as Promise<unknown>).catch((e: unknown) => {
				console.warn("[i18n] setLocale main-process push failed:", e);
			});
		}
	} catch (e: unknown) {
		console.warn("[i18n] setLocale main-process push failed:", e);
	}
}

/**
 * Best-effort push of the current locale + renderer-known tray-menu
 * labels to the Python backend via the ``set_tray_locale`` IPC message.
 * The backend uses the pushed locale + labels to localise the tray
 * menu (see ``voice_typer/server/tray_i18n.py``).
 *
 * No-op when the bridge is missing (Tauri host, module-init scenario).
 * Rejections and sync throws are caught and logged via ``console.warn``.
 *
 * The label map is built by {@link trayLabelsForLocale}.
 */
export function pushLocaleToPythonBackend(locale: Locale): void {
	try {
		// Read directly from the globally-augmented ``window.python``
		// (declared in ``types/ipc/bubble_bridge.ts``) instead of
		// re-declaring the bridge shape inline via an
		// ``as unknown as { python?: ... }`` cast.
		const result = window.python?.call?.({
			type: "set_tray_locale",
			data: { locale, labels: trayLabelsForLocale() },
		});
		if (result && typeof (result as Promise<unknown>).then === "function") {
			(result as Promise<unknown>).catch((e: unknown) => {
				console.warn("[i18n] setLocale Python-backend push failed:", e);
			});
		}
	} catch (e: unknown) {
		console.warn("[i18n] setLocale Python-backend push failed:", e);
	}
}
