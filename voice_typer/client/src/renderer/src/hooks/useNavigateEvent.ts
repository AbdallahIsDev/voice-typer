import type { NavigateOptions } from "@/hooks/useNavigation";
import { usePythonEvent } from "@/hooks/usePython";
import { isKnownPage } from "@/router/routes";
import type { Page } from "@/types/ipc";

/** Dependencies wired by the App entry component. */
export interface UseNavigateEventOptions {
	navigate: (page: Page, opts?: NavigateOptions) => void;
}

export function useNavigateEvent({ navigate }: UseNavigateEventOptions): void {
	usePythonEvent("navigate", (data): (() => void) | undefined => {
		const navData = (data ?? {}) as Record<string, unknown>;
		const path = typeof navData.path === "string" ? navData.path : undefined;
		if (path) {
			const page = path.replace(/^\//, "");
			if (isKnownPage(page)) {
				const consentField =
					typeof navData.consent_field === "string"
						? navData.consent_field
						: undefined;
				const targetPage: Page =
					consentField && page === "settings"
						? "settingsPrivacy"
						: (page as Page);
				navigate(targetPage, consentField ? { consentField } : undefined);
			} else {
				console.warn(
					`[renderer:useNavigateEvent] ignoring unknown page path: "${page}"`,
				);
			}
		}
		return undefined;
	});
}
