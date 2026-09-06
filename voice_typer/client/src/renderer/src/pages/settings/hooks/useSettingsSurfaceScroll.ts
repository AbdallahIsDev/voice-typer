// Settings surface scroll-restore hook.
//
// Extracted from `pages/Settings.tsx` (page-root slimming): when the
// active surface changes (hub ↔ section page ↔ another section page),
// the surface's previously-saved scroll offset is restored on the
// shared scroll container. The per-surface offset memory
// (`scrollPositionsRef`) is OWNED BY THE PAGE and passed in here — the
// consent deep-link consumption (see `useSettingsDeepLinks`) writes a
// zeroed Privacy offset into the same ref BEFORE this hook's effect
// reads it, so the page must call `useSettingsDeepLinks` first.

import { useEffect, useRef } from "react";
import type { Page } from "@/types/ipc";

export interface UseSettingsSurfaceScrollOptions {
	/** The active Settings surface page literal (route-switch prop). */
	page: Page;
	/**
	 * Per-surface scroll-offset memory, keyed by the raw page literal
	 * (including the hub's "settings") so every surface remembers its
	 * own scroll offset. Owned by the page; shared with the deep-link
	 * hook (which zeroes the consent target's saved offset).
	 */
	scrollPositionsRef: React.RefObject<Record<string, number>>;
}

/**
 * Restores the saved scroll position whenever the active surface
 * changes. See the file header for the extraction rationale.
 */
export function useSettingsSurfaceScroll({
	page,
	scrollPositionsRef,
}: UseSettingsSurfaceScrollOptions): void {
	// The scroll-positions ref survives across surface transitions
	// inside the Settings component instance (hub ↔ section pages share
	// it via the `page` prop swap), so the per-surface scroll-restore
	// behavior is preserved.
	const prevSurfaceRef = useRef<Page>(page);

	// Restore scroll position when the active surface changes (hub ↔
	// section page ↔ another section page).
	useEffect(() => {
		if (prevSurfaceRef.current !== page) {
			prevSurfaceRef.current = page;
			const saved = scrollPositionsRef.current[page] ?? 0;
			if (saved > 0) {
				requestAnimationFrame(() => {
					const mainEl = document.getElementById("main-content");
					if (mainEl) mainEl.scrollTop = saved;
				});
			}
		}
	}, [page, scrollPositionsRef]);
}
