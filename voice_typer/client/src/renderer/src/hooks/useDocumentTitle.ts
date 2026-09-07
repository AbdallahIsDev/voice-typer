/**
 * useDocumentTitle — keeps ``document.title`` in sync with the active
 * route (a11y / WCAG 2.4.2 Page Titled).
 *
 * Extracted from App.tsx (the entry component stays pure wiring) using
 * the same extraction pattern as the other extracted use* hooks.
 * Behaviour is byte-identical to the original inline effect.
 *
 * Screen-reader users (who announce the window title to orient) and OS
 * taskbar users can tell which page is active without reading into
 * main content. The title is composed as ``t("nav.<page>") — APP_NAME``
 * so it localises with the rest of the UI. Settings surfaces pull
 * their title from the SECTION REGISTRY (settingsSections.ts) instead
 * of ``nav.*`` duplicates — the hub row, the nested page's card
 * heading, and the window title all read the SAME key, so they can
 * never drift. The effect runs on mount AND whenever ``currentPage``
 * or ``t`` (i.e. the active locale) changes — a locale switch
 * re-titles the window.
 */

import { useEffect } from "react";

import { APP_NAME } from "@/branding";
import {
	isSettingsSurface,
	SECTION_TITLE_BY_PAGE,
} from "@/components/settings/settingsSections";
import type { Page } from "@/types/ipc";

/** Dependencies wired by the App entry component. */
export interface UseDocumentTitleOptions {
	/** The live route, from ``useNavigation``. */
	currentPage: Page;
	/** Locale-reactive translate function (from ``useT``). */
	t: (key: string, params?: Record<string, string>) => string;
}

/**
 * Write the localised ``<page> — <app>`` title to ``document.title``.
 * Call once at the top level of the App component.
 */
export function useDocumentTitle({
	currentPage,
	t,
}: UseDocumentTitleOptions): void {
	useEffect(() => {
		const pageTitle = isSettingsSurface(currentPage)
			? currentPage === "settings"
				? t("settings.title")
				: t(SECTION_TITLE_BY_PAGE[currentPage])
			: t(`nav.${currentPage}`);
		document.title = `${pageTitle} — ${APP_NAME}`;
	}, [currentPage, t]);
}
