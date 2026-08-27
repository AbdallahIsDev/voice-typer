import { memo, useState } from "react";
import { SearchField } from "@/components/common/SearchField";
import { t } from "@/i18n/i18n";
import type { Page } from "@/types/ipc";

/**
 * Global search bar — lives in the title bar's middle spacer.
 *
 * UI-ONLY PROTOTYPE (no wiring): the field renders with the correct
 * per-page placeholder and appears only on searchable pages, but typing
 * does not filter anything yet. The query state is local to the bar so
 * the clear button and placeholder swap behave visually.
 *
 * Searchable pages mirror the pages that today render their own
 * per-page SearchField:
 *   - history            → t("history.searchPlaceholder")        "Search History"
 *   - templates          → t("templates.searchPlaceholder")      "Search templates…"
 *   - vocabulary         → t("vocabulary.searchPlaceholderCount") "Search {count} corrections"
 *   - settings* (parent + 4 subpages) → t("settings.searchPlaceholder") "Search settings…"
 * All other pages (home, models, microphone, analytics, aboutAndPrivacy,
 * onboarding) hide the bar entirely — no search exists there.
 */
const SEARCHABLE_PAGES: ReadonlySet<Page> = new Set<Page>([
	"history",
	"templates",
	"vocabulary",
	"settings",
	"settingsGeneral",
	"settingsAiAudio",
	"settingsAppearance",
	"settingsPrivacy",
]);

/** Placeholder shown per searchable page. Uses the SAME i18n keys the
 *  per-page SearchFields use today, so translations stay in one place. */
function placeholderFor(page: Page): string {
	switch (page) {
		case "history":
			return t("history.searchPlaceholder");
		case "templates":
			return t("templates.searchPlaceholder");
		case "vocabulary":
			// PROTOTYPE: count is a fixed stand-in ("42"). The real
			// corrections count will be wired in with the search wiring.
			return t("vocabulary.searchPlaceholderCount", { count: "42" });
		default:
			return t("settings.searchPlaceholder");
	}
}

interface GlobalSearchBarProps {
	currentPage: Page;
}

export const GlobalSearchBar = memo(function GlobalSearchBar({
	currentPage,
}: GlobalSearchBarProps) {
	const [query, setQuery] = useState("");

	if (!SEARCHABLE_PAGES.has(currentPage)) {
		return null;
	}

	const placeholder = placeholderFor(currentPage);

	return (
		// no-drag ONLY on the field itself: the title bar root is
		// -webkit-app-region: drag. A full-width no-drag wrapper around
		// the field would cover the entire middle strip and kill window
		// dragging there — the surrounding flex-1 spacer stays draggable.
		<div className="no-drag w-72">
			<SearchField
				value={query}
				onChange={setQuery}
				placeholder={placeholder}
				ariaLabel={placeholder}
				// Compact for the 32px title bar: Input defaults to h-8
				// (full bar height); h-6 (24px) leaves ~4px of breathing
				// room above and below, and the parent's items-center
				// vertically centers it.
				className="h-6 rounded-lg"
			/>
		</div>
	);
});
