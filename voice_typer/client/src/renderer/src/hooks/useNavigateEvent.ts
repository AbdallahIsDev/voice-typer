/**
 * useNavigateEvent — routes backend ``navigate`` push events into the
 * shared navigation store.
 *
 * Extracted from App.tsx (the entry component stays pure wiring) using
 * the same extraction pattern as the ``use*Toast`` hooks. Behaviour is
 * byte-identical to the original inline
 * ``usePythonEvent("navigate", ...)`` block:
 *
 *   - Page validation uses the single route table in
 *     ``router/routes.ts`` via ``isKnownPage`` (previously a
 *     hand-maintained ``pageMap`` had drifted and silently dropped
 *     unknown-but-real pages like ``onboarding``).
 *   - ``consent_field`` — deep-link to a specific Settings consent row
 *     (CLICKABLE OS notifications: the main process broadcasts
 *     ``navigate {path: "/settings", consent_field}`` when the user
 *     clicks the toast; Settings consumes the ``consentField`` option
 *     and scrolls to / highlights the exact toggle).
 *   - When the legacy ``"settings"`` parent literal is sent WITH a
 *     ``consent_field``, the deep-link must land on the Privacy
 *     sub-page (where the consent toggles live), not the General
 *     default. The ``useNavigation.navigate`` action redirects bare
 *     ``"settings"`` to ``"settingsGeneral"`` — so the target is
 *     overridden to ``"settingsPrivacy"`` when a ``consent_field`` is
 *     present (the user's intent is "open the consent row", not "open
 *     Settings General"). The ``pendingConsentField`` transient field
 *     carries the row hint to the Privacy sub-page via the same
 *     navigate call.
 */

import type { NavigateOptions } from "@/hooks/useNavigation";
import { usePythonEvent } from "@/hooks/usePython";
import { isKnownPage } from "@/router/routes";
import type { Page } from "@/types/ipc";

/** Dependencies wired by the App entry component. */
export interface UseNavigateEventOptions {
	/**
	 * Shared-store navigation action (from ``useNavigation``) that
	 * the backend-driven route lands in.
	 */
	navigate: (page: Page, opts?: NavigateOptions) => void;
}

/**
 * Subscribe to ``navigate`` push events and route them through the
 * shared navigation store. Call once at the top level of the App
 * component; the subscription lives for the component's lifetime.
 */
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
