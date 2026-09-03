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
		// The tray tooltip's "no model selected" state message is the
		// SAME localized text as the Home status pill's hint: the server
		// key ``state.model_manager.no_model_selected`` is filled from
		// the renderer's ``home.noModelSelectedHint`` so both surfaces
		// agree verbatim in every locale (server keeps its matching
		// English fallback before the first push).
		["state.model_manager.no_model_selected", "home.noModelSelectedHint"],
		// Every remaining server tray STATE message (the ``state.*``
		// keys in ``voice_typer/server/i18n.py`` ``_INITIAL_LABELS``)
		// is pushed from the renderer's ``trayState.*`` translations so
		// the whole tray tooltip follows the renderer locale — not just
		// the no-model-selected state. Keys with ``{placeholder}``
		// tokens (``{device_info}``, ``{name}``, ``{backend}``,
		// ``{error}``) keep the exact token name so the server's
		// ``i18n.t(key, ...)`` formatting still interpolates at call
		// time. Entries whose translation is missing resolve to the raw
		// key and are skipped below, so the server keeps its English
		// fallback until every locale is translated.
		//
		// AppState value labels — the tooltip's fallback state suffix
		// (``_compute_tooltip`` renders ``state.<value>`` when no
		// per-call message is set).
		["state.idle", "trayState.idle"],
		["state.recording", "trayState.recording"],
		["state.transcribing", "trayState.transcribing"],
		["state.loading", "trayState.loading"],
		["state.error", "trayState.error"],
		["state.cancelling", "trayState.cancelling"],
		// ── recording_controller state messages ───────────────────────
		[
			"state.recording_controller.loading_queued",
			"trayState.recordingController.loadingQueued",
		],
		[
			"state.recording_controller.starting_up",
			"trayState.recordingController.startingUp",
		],
		[
			"state.recording_controller.consent_required",
			"trayState.recordingController.consentRequired",
		],
		[
			"state.recording_controller.model_failed_retry",
			"trayState.recordingController.modelFailedRetry",
		],
		[
			"state.recording_controller.recording",
			"trayState.recordingController.recording",
		],
		[
			"state.recording_controller.recording_failed",
			"trayState.recordingController.recordingFailed",
		],
		[
			"state.recording_controller.recording_failed_permission",
			"trayState.recordingController.recordingFailedPermission",
		],
		[
			"state.recording_controller.recording_failed_no_device",
			"trayState.recordingController.recordingFailedNoDevice",
		],
		[
			"state.recording_controller.stop_failed",
			"trayState.recordingController.stopFailed",
		],
		[
			"state.recording_controller.too_short",
			"trayState.recordingController.tooShort",
		],
		[
			"state.recording_controller.transcribing",
			"trayState.recordingController.transcribing",
		],
		[
			"state.recording_controller.cancelling",
			"trayState.recordingController.cancelling",
		],
		[
			"state.recording_controller.cancelled",
			"trayState.recordingController.cancelled",
		],
		[
			"state.recording_controller.recovered",
			"trayState.recordingController.recovered",
		],
		[
			"state.recording_controller.still_transcribing",
			"trayState.recordingController.stillTranscribing",
		],
		// ── model_manager state messages ──────────────────────────────
		["state.model_manager.loading", "trayState.modelManager.loading"],
		[
			"state.model_manager.ready_whisper",
			"trayState.modelManager.readyWhisper",
		],
		["state.model_manager.ready_other", "trayState.modelManager.readyOther"],
		[
			"state.model_manager.load_failed_retry",
			"trayState.modelManager.loadFailedRetry",
		],
		[
			"state.model_manager.backend_failed",
			"trayState.modelManager.backendFailed",
		],
		["state.model_manager.model_failed", "trayState.modelManager.modelFailed"],
		[
			"state.model_manager.model_not_downloaded",
			"trayState.modelManager.modelNotDownloaded",
		],
		[
			"state.model_manager.model_integrity_failed",
			"trayState.modelManager.modelIntegrityFailed",
		],
		// ── dictation pipeline state messages ─────────────────────────
		[
			"state.dictation_pipeline.clipboard_unavailable",
			"trayState.pipeline.clipboardUnavailable",
		],
		[
			"state.dictation_pipeline.no_speech_detected",
			"trayState.pipeline.noSpeechDetected",
		],
		[
			"state.dictation_pipeline.no_speech_check_mic",
			"trayState.pipeline.noSpeechCheckMic",
		],
		[
			"state.dictation_pipeline.transcription_empty",
			"trayState.pipeline.transcriptionEmpty",
		],
		// paste_step "Done -- N chars (mode)" statuses — dynamic
		// per-transcription character count; the server formats
		// ``{count}`` at call time.
		["state.dictation_pipeline.done_pasted", "trayState.pipeline.donePasted"],
		["state.dictation_pipeline.done_in_db", "trayState.pipeline.doneInDb"],
		[
			"state.dictation_pipeline.done_in_clipboard",
			"trayState.pipeline.doneInClipboard",
		],
		// ── tray NOTIFICATION messages (``notify.*``) ───────────────────
		// Every server tray-notification key that has a live call site
		// (``i18n.t("notify...")`` in the Python sidecar) is pushed from
		// the renderer's ``notify.*`` translations so OS notifications
		// follow the renderer locale the same way the tooltip state
		// messages do. The renderer key names mirror the server keys
		// 1:1 and the English values are byte-identical to the server's
		// ``_INITIAL_LABELS`` fallback, so the English path is unchanged
		// and non-English locales get the translated text. Placeholder
		// tokens (``{model}``, ``{error}``, ``{label}``,
		// ``{backend}``, ``{char_count}``) match the server's
		// ``i18n.t(key, ...)`` format args.
		[
			"notify.recording_controller.cancelled_timeout",
			"notify.recording_controller.cancelled_timeout",
		],
		[
			"notify.recording_controller.start_failed",
			"notify.recording_controller.start_failed",
		],
		[
			"notify.recording_controller.start_failed_with_reason",
			"notify.recording_controller.start_failed_with_reason",
		],
		[
			"notify.recording_controller.stop_failed",
			"notify.recording_controller.stop_failed",
		],
		[
			"notify.recording_controller.silence_warning",
			"notify.recording_controller.silence_warning",
		],
		[
			"notify.recording_controller.consent_required",
			"notify.recording_controller.consent_required",
		],
		[
			"notify.recording_controller.max_duration_auto_stop",
			"notify.recording_controller.max_duration_auto_stop",
		],
		[
			"notify.recording_controller.mic_disconnected",
			"notify.recording_controller.mic_disconnected",
		],
		[
			"notify.recording_controller.mic_unplugged",
			"notify.recording_controller.mic_unplugged",
		],
		[
			"notify.recording_controller.still_running",
			"notify.recording_controller.still_running",
		],
		[
			"notify.recording_controller.xrun_title",
			"notify.recording_controller.xrun_title",
		],
		[
			"notify.recording_controller.xrun_body",
			"notify.recording_controller.xrun_body",
		],
		[
			"notify.recording_controller.mic_permission_revoked",
			"notify.recording_controller.mic_permission_revoked",
		],
		[
			"notify.recording_controller.silence_auto_stop",
			"notify.recording_controller.silence_auto_stop",
		],
		[
			"notify.model_manager.backend_change_deferred",
			"notify.model_manager.backend_change_deferred",
		],
		[
			"notify.model_manager.change_deferred",
			"notify.model_manager.change_deferred",
		],
		["notify.model_manager.load_failed", "notify.model_manager.load_failed"],
		[
			"notify.model_manager.load_failed_critical",
			"notify.model_manager.load_failed_critical",
		],
		[
			"notify.model_manager.backend_init_failed",
			"notify.model_manager.backend_init_failed",
		],
		[
			"notify.model_manager.last_resort_unloaded",
			"notify.model_manager.last_resort_unloaded",
		],
		[
			"notify.model_manager.model_not_downloaded",
			"notify.model_manager.model_not_downloaded",
		],
		[
			"notify.model_manager.no_model_selected",
			"notify.model_manager.no_model_selected",
		],
		// ── permissions notifications (macOS/Linux permission prompts)
		// The renderer values use the ``{appName}`` brand placeholder
		// (C-BRAND-1); the server formats ``{app}`` with the same app
		// name at call time, and ``{command}`` stays a literal token.
		["notify.permissions.macos_title", "notify.permissions.macos_title"],
		["notify.permissions.macos_body", "notify.permissions.macos_body"],
		[
			"notify.permissions.macos_body_with_command",
			"notify.permissions.macos_body_with_command",
		],
		["notify.permissions.linux_title", "notify.permissions.linux_title"],
		["notify.permissions.linux_body", "notify.permissions.linux_body"],
		[
			"notify.settings_controller.autostart_failed",
			"notify.settings_controller.autostart_failed",
		],
		[
			"notify.settings_controller.mic_changed",
			"notify.settings_controller.mic_changed",
		],
		[
			"notify.settings_controller.mic_next_recording",
			"notify.settings_controller.mic_next_recording",
		],
		[
			"notify.settings_controller.mic_save_failed",
			"notify.settings_controller.mic_save_failed",
		],
		["notify.app.repaste_no_previous", "notify.app.repaste_no_previous"],
		["notify.app.repaste_copy_failed", "notify.app.repaste_copy_failed"],
		["notify.app.repaste_done", "notify.app.repaste_done"],
		["notify.app.repaste_blocked", "notify.app.repaste_blocked"],
		["notify.app.undo_nothing", "notify.app.undo_nothing"],
		["notify.app.undo_done", "notify.app.undo_done"],
		["notify.app.undo_no_pynput", "notify.app.undo_no_pynput"],
		["notify.app.undo_failed", "notify.app.undo_failed"],
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
 * No-op when the bridge is missing (module-init scenario where neither
 * the Electron preload nor the Tauri bridge has installed ``window_``
 * yet). Under both runtimes the push is a plain resolve: Electron
 * stores the locale in its main process, and the Tauri host stores it
 * in ``SidecarState::host_locale`` via the ``set_host_locale``
 * command. Rejections and sync throws are caught and logged via
 * ``console.warn`` so a locale switch never crashes the renderer.
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
				console.warn("[renderer:i18n] setLocale main-process push failed:", e);
			});
		}
	} catch (e: unknown) {
		console.warn("[renderer:i18n] setLocale main-process push failed:", e);
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
				console.warn(
					"[renderer:i18n] setLocale Python-backend push failed:",
					e,
				);
			});
		}
	} catch (e: unknown) {
		console.warn("[renderer:i18n] setLocale Python-backend push failed:", e);
	}
}
