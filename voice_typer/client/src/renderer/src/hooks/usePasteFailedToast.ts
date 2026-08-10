/**
 * usePasteFailedToast — surfaces backend ``paste_failed`` events as
 * sonner toasts.
 *
 * Extracted from App.tsx (EO-28, Phase 4.5 spaghetti split) to keep
 * App.tsx a pure layout shell. Behaviour is byte-identical to the
 * original inline ``usePythonEvent("paste_failed", ...)`` block:
 *
 *   - The action button label was a hardcoded English string
 *     ("Copy path") which broke i18n for non-English users. Wired
 *     through ``t("common.copyPath")`` so the label resolves to the
 *     active locale's translation.
 *   - If the backend supplies a ``recovery_path``, the toast shows a
 *     "Copy path" action that writes it to the clipboard (best-effort
 *     — clipboard API may be unavailable, non-fatal).
 *   - The message may be multi-line: the first line becomes the toast
 *     title, the rest the description.
 */

import { toast } from "sonner";
import { usePythonEvent } from "@/hooks/usePython";

/** Minimal `t` function type matching i18n.t's signature. */
type TFn = (key: string, params?: Record<string, string>) => string;

/**
 * Subscribe to ``paste_failed`` push events and render the recovery
 * toast. Call once at the top level of a component; the subscription
 * lives for the component's lifetime.
 */
export function usePasteFailedToast(t: TFn): void {
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
			toast.warning(title, {
				description,
				duration: 8000,
				action: {
					label: t("common.copyPath"),
					onClick: () => {
						try {
							navigator.clipboard
								?.writeText(recoveryPath)
								.catch((err) =>
									console.warn("[clipboard] writeText failed:", err),
								);
						} catch (e) {
							// clipboard API may be unavailable — non-fatal.
							console.warn(
								"[App] clipboard writeText (recovery path) failed:",
								e,
							);
						}
					},
				},
			});
		} else {
			toast.warning(title, { description, duration: 8000 });
		}
		return undefined;
	});
}
