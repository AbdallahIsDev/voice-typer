// Settings reset-to-defaults hook.
//
// Extracted from `pages/Settings.tsx` (page-root slimming): the
// reset-to-defaults event flow — confirm-dialog state + the async
// `get_defaults` → filter → `update_config` → toast sequence — was the
// page's largest remaining inline event handler. It lives here so the
// page root stays layout + wiring, and the flow is testable in
// isolation.
//
// The protected-keys blocklist encodes one-time state (schema version,
// onboarding flag, OS-specific warning dismissal) that must survive a
// factory reset of user-tunable preferences. Hoisted to module scope so
// `resetToDefaults` can be a stable useCallback without re-allocating
// the list each render.

import { useCallback, useState } from "react";
import type { usePython } from "@/hooks/usePython";
import type { ShowSnackOptions, SnackbarType } from "@/hooks/useSnackbar";
import { t } from "@/i18n/i18n";
import { userFacingErrorMessage } from "@/lib/errors/userFacingErrorMessage";
import type { VoiceTyperConfig } from "@/types/config";

const CONFIG_PROTECTED_KEYS = [
	"schema_version",
	"wayland_warned",
	"onboarding_completed",
] as const;

export interface UseSettingsResetOptions {
	/** The currently-loaded config (reset is a no-op while `null`). */
	config: VoiceTyperConfig | null;
	/** The Python bridge call (from usePython). */
	call: ReturnType<typeof usePython>["call"];
	/** Config write callback (from useSettingsConfig). */
	updateConfig: (updates: Partial<VoiceTyperConfig>) => Promise<void>;
	/** Toast callback (from useSnackbar). */
	showSnack: (
		message: string,
		type?: SnackbarType,
		options?: ShowSnackOptions,
	) => void;
}

export interface UseSettingsResetReturn {
	/** Confirm-dialog visibility state (rendered by the page). */
	showResetDialog: boolean;
	setShowResetDialog: React.Dispatch<React.SetStateAction<boolean>>;
	/** The confirmed reset action (ConfirmDialog's onConfirm). */
	resetToDefaults: () => Promise<void>;
}

/**
 * Reset-to-defaults flow for the Settings page: confirm-dialog state +
 * the guarded defaults fetch/apply. See the file header for the
 * extraction rationale.
 */
export function useSettingsReset({
	config,
	call,
	updateConfig,
	showSnack,
}: UseSettingsResetOptions): UseSettingsResetReturn {
	const [showResetDialog, setShowResetDialog] = useState(false);

	// reset-to-defaults wrapped in useCallback so ConfirmDialog's
	// `onConfirm` prop identity stays stable across renders. The
	// CONFIG_PROTECTED_KEYS blocklist (above) is hoisted to module scope so
	// it doesn't need to be a dep.
	const resetToDefaults = useCallback(async () => {
		if (!config) return;
		setShowResetDialog(false);
		try {
			const defaults = await call<Record<string, unknown>>("get_defaults");
			if (defaults && typeof defaults === "object") {
				const safeDefaults: Record<string, unknown> = {};
				for (const [key, value] of Object.entries(defaults)) {
					if (value === "<redacted>") continue;
					if ((CONFIG_PROTECTED_KEYS as readonly string[]).includes(key))
						continue;
					safeDefaults[key] = value;
				}
				await updateConfig(safeDefaults as Partial<VoiceTyperConfig>);
				showSnack(t("settings.resetToDefaultsToast"), "success");
			} else {
				showSnack(t("settings.fetchDefaultsFailed"), "error");
			}
		} catch (err) {
			console.error("[renderer:Settings] Failed to reset to defaults:", err);
			// Known failure classes (timeout / backend unreachable) get
			// their curated localized message; unknown ones keep the
			// contextual fallback.
			showSnack(
				userFacingErrorMessage(err, t, t("settings.resetFailed")),
				"error",
			);
		}
	}, [config, call, updateConfig, showSnack]);

	return { showResetDialog, setShowResetDialog, resetToDefaults };
}
