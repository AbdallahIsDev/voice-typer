// src/renderer/src/components/consent/ConsentGateDialog.tsx
//
// Unified point-of-use consent dialog ("This sends your audio to Groq.
// Allow? [Allow / Cancel]"). Mounted ONCE in App.tsx; any consent-
// gated flow opens it via `openConsentGate()` (see lib/consentGate.ts).
//
// Behaviour:
//   - Allow → persists the consent field via the allowlisted
//     `set_config` IPC (SEC-002), then invokes the request's `onAllow`
//     retry (e.g. re-start dictation, re-run the download), then
//     closes. If persistence fails, the dialog stays open with an
//     error toast — the UI never claims consent was granted when the
//     backend rejected it.
//   - Cancel → closes. No consent is granted.
//   - "Open Settings" → deep-links to the exact consent row
//     (Settings consumes the `consentField` navigate option), closes.
//
// The OS-level equivalent (clickable native toast → Settings) is the
// backend's `notification` event with `click_consent_field`; both
// paths land on the same Settings row.

import { useCallback } from "react";
import {
	AlertDialog,
	AlertDialogCancel,
	AlertDialogContent,
	AlertDialogDescription,
	AlertDialogFooter,
	AlertDialogHeader,
	AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { useNavigation } from "@/hooks/useNavigation";
import { usePython } from "@/hooks/usePython";
import { useSnackbar } from "@/hooks/useSnackbar";
import { useT } from "@/i18n/i18n";
import { useConsentGateStore } from "@/lib/consentGate";

export default function ConsentGateDialog() {
	const { call } = usePython();
	const { navigate } = useNavigation();
	const { showSnack } = useSnackbar();
	const t = useT();
	const request = useConsentGateStore((s) => s.request);
	const close = useConsentGateStore((s) => s.close);

	const open = request !== null;

	const handleAllow = useCallback(async () => {
		if (!request) return;
		try {
			await call("set_config", { [request.consentField]: true });
		} catch (err) {
			console.error(
				"[renderer:ConsentGateDialog] set_config consent failed:",
				err,
			);
			showSnack(t("consentDialog.persistFailed"), "error");
			// Keep the dialog open — the grant did not persist, so the
			// UI must not claim it did (mirrors the onboarding Done-step
			// consent checkbox revert-on-failure contract).
			return;
		}
		close();
		if (request.onAllow) {
			try {
				await request.onAllow();
			} catch (err) {
				console.error(
					"[renderer:ConsentGateDialog] consent retry failed:",
					err,
				);
				// Non-fatal: the consent is granted; the user can simply
				// try the action again. Surface a hint so the failure
				// isn't silent.
				showSnack(t("consentDialog.retryFailed"), "warning");
			}
		}
	}, [request, call, close, showSnack, t]);

	const handleOpenSettings = useCallback(() => {
		if (!request) return;
		close();
		navigate("settings", { consentField: request.consentField });
	}, [request, close, navigate]);

	if (!request) return null;

	return (
		<AlertDialog open={open} onOpenChange={(isOpen) => !isOpen && close()}>
			<AlertDialogContent>
				<AlertDialogHeader>
					<AlertDialogTitle>{t("consentDialog.title")}</AlertDialogTitle>
					<AlertDialogDescription>
						{t(request.bodyKey, request.bodyParams)}
					</AlertDialogDescription>
				</AlertDialogHeader>
				<AlertDialogFooter>
					<Button type="button" variant="ghost" onClick={handleOpenSettings}>
						{t("consentDialog.openSettings")}
					</Button>
					<AlertDialogCancel>{t("consentDialog.cancel")}</AlertDialogCancel>
					{/* Plain Button (NOT AlertDialogAction): Radix's action
					    auto-closes the dialog on click, which would defeat
					    the keep-open-on-persist-failure contract below. The
					    dialog closes only via the explicit ``close()`` in
					    handleAllow / handleOpenSettings / Cancel / Escape. */}
					<Button type="button" onClick={handleAllow}>
						{t("consentDialog.allow")}
					</Button>
				</AlertDialogFooter>
			</AlertDialogContent>
		</AlertDialog>
	);
}
