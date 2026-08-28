import { memo, useEffect, useRef } from "react";
import { SearchField } from "@/components/common/SearchField";
import { useGlobalSearch } from "@/hooks/useGlobalSearch";
import { t } from "@/i18n/i18n";
import type { Page } from "@/types/ipc";

/**
 * Global search bar — lives in the title bar's middle spacer.
 *
 * Fully wired: the query state lives in the shared `useGlobalSearch`
 * store. Each searchable page reads the query from that store and
 * filters/loads its data, and the per-page SearchFields have been
 * removed — this is the ONLY search input in the app.
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

function isSettingsPage(page: Page): boolean {
	return (
		page === "settings" ||
		page === "settingsGeneral" ||
		page === "settingsAiAudio" ||
		page === "settingsAppearance" ||
		page === "settingsPrivacy"
	);
}

/**
 * Stable group identity for query-reset purposes. Settings' 4 subpages
 * share one search (and its auto-switch navigates BETWEEN them while
 * preserving the query) — so the query must NOT reset on subpage
 * navigation, only when leaving the whole Settings group.
 */
function searchGroup(page: Page): string {
	if (isSettingsPage(page)) return "settings";
	return page;
}

/** Placeholder shown per searchable page. Uses the SAME i18n keys the
 *  per-page SearchFields used, so translations stay in one place. */
function placeholderFor(page: Page, vocabEntryCount: number): string {
	switch (page) {
		case "history":
			return t("history.searchPlaceholder");
		case "templates":
			return t("templates.searchPlaceholder");
		case "vocabulary":
			return t("vocabulary.searchPlaceholderCount", {
				count: String(vocabEntryCount),
			});
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
	const query = useGlobalSearch((s) => s.query);
	const setQuery = useGlobalSearch((s) => s.setQuery);
	const clearQuery = useGlobalSearch((s) => s.clearQuery);
	const vocabEntryCount = useGlobalSearch((s) => s.vocabEntryCount);
	const inputRef = useRef<HTMLInputElement>(null);

	// Reset the query when navigating to a different search GROUP
	// (page), so each page starts clean. Settings subpage↔subpage
	// navigation keeps the query — its auto-switch relies on it.
	const prevGroupRef = useRef<string>(searchGroup(currentPage));
	useEffect(() => {
		const group = searchGroup(currentPage);
		if (prevGroupRef.current !== group) {
			prevGroupRef.current = group;
			clearQuery();
		}
	}, [currentPage, clearQuery]);

	const searchable = SEARCHABLE_PAGES.has(currentPage);

	// Ctrl+K (Cmd+K on Mac) focuses the global search. Registered only
	// when the current page is searchable (otherwise there's nothing to
	// focus).
	useEffect(() => {
		if (!searchable) return;
		const onKeyDown = (e: KeyboardEvent) => {
			if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
				e.preventDefault();
				inputRef.current?.focus();
			}
		};
		window.addEventListener("keydown", onKeyDown);
		return () => window.removeEventListener("keydown", onKeyDown);
	}, [searchable]);

	if (!searchable) {
		return null;
	}

	const placeholder = placeholderFor(currentPage, vocabEntryCount);

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
				inputRef={inputRef}
				// Compact for the 36px title bar: Input defaults to h-7
				// (full bar height) + text-base; h-6.5 (26px) leaves ~5px of
				// breathing room above and below, and text-xs keeps the
				// placeholder/label from overflowing the small field. The
				// parent's items-center vertically centers it.
				className="h-6.5 rounded-lg text-xs md:text-xs"
			/>
		</div>
	);
});
