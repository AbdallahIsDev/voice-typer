import { usePythonEvent } from "@/hooks/usePython";
import type { TranslateFn } from "@/i18n/translate-types";
import { SNACKBAR_DEFAULT_DURATION_MS, useSnackbar } from "./useSnackbar";

export function usePasteFailedToast(t: TranslateFn): void {
	const { showSnack } = useSnackbar();
	usePythonEvent("paste_failed", (data): (() => void) | undefined => {
		const payload = (data ?? {}) as {
			message?: string;
			recovery_path?: string | null;
		};
		const message = payload.message ?? t("home.pasteFailedMessage");
		const recoveryPath =
			typeof payload.recovery_path === "string" ? payload.recovery_path : null;
		const lines = message.split("\n");
		const title = lines[0] ?? message;
		const description = lines.slice(1).join("\n") || undefined;
		if (recoveryPath) {
			showSnack(title, "warning", {
				description,
				duration: SNACKBAR_DEFAULT_DURATION_MS.error,
				action: {
					label: t("common.copyPath"),
					onClick: () => {
						try {
							navigator.clipboard
								?.writeText(recoveryPath)
								.catch((err) =>
									console.warn(
										"[renderer:usePasteFailedToast] writeText failed:",
										err,
									),
								);
						} catch (e) {
							// clipboard API may be unavailable, non-fatal.
							console.warn(
								"[renderer:usePasteFailedToast] clipboard writeText (recovery path) failed:",
								e,
							);
						}
					},
				},
			});
		} else {
			showSnack(title, "warning", {
				description,
				duration: SNACKBAR_DEFAULT_DURATION_MS.error,
			});
		}
		return undefined;
	});
}
