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
		document.title = `${pageTitle}, ${APP_NAME}`;
	}, [currentPage, t]);
}
