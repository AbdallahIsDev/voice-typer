// Renderer copy map for media-transcribe failure codes (ADR-0023 E13):
// the backend's raw message is diagnostics, never shown verbatim — the
// user gets the localized, actionable line for the failure class.

import type { TranslationKey } from "@/i18n/translation-keys";

const MEDIA_ERROR_KEYS: Readonly<Record<string, TranslationKey>> = {
	drm_refused: "media.errorDrm",
	no_audio: "media.errorNoAudio",
	unsupported_live: "media.errorLive",
	login_required: "media.errorLoginRequired",
	geo_blocked: "media.errorGeoBlocked",
	playlist_rejected: "media.errorPlaylist",
	decode_failed: "media.errorDecode",
	resolve_failed: "media.errorResolve",
	// A stalled stream ends in the same recovery advice as a failed
	// download: check the connection and retry.
	stream_stalled: "media.errorDownloadFailed",
	url_expired: "media.errorUrlExpired",
	download_failed: "media.errorDownloadFailed",
	no_engine_loaded: "media.errorNoModel",
	job_busy: "media.errorBusy",
};

/** Localized copy for a backend media/error code; `null` when unknown. */
export function mediaErrorKey(
	code: string | null | undefined,
): TranslationKey | null {
	if (!code) return null;
	return MEDIA_ERROR_KEYS[code] ?? null;
}

/** True when the failure means "no model loaded" (offers Open Models). */
export function isNoModelError(code: string | null | undefined): boolean {
	return code === "no_engine_loaded" || code === "server.no_model";
}
