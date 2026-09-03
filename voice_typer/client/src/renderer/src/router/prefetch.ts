// Route-chunk prefetching (vercel-react-best-practices: bundle-preload).
//
// The 8 secondary routes are React.lazy chunks (see PageSwitch.tsx).
// Without prefetching, the FIRST navigation to each page waits on a
// dynamic import before anything renders. This module closes that gap:
//
//   1. `prefetchRouteChunks()` — called once from App after mount, on
//      `requestIdleCallback`, warms every route chunk. The app is a
//      desktop shell (local files, small chunks), so warming all of
//      them at idle is effectively free and makes every subsequent
//      route switch render synchronously from React.lazy's module
//      cache — no Suspense fallback at all.
//   2. `prefetchPage(page)` — intent-based backup for the idle pass:
//      the Sidebar calls it on nav-item hover/focus so the chunk is
//      already streaming before the click lands.
//
// Fire-and-forget: a failed prefetch (dev HMR race, crash) must never
// surface — the normal lazy import path still handles the load.

import type { Page } from "@/types/ipc";

// Same dynamic imports PageSwitch lazily uses — Vite emits one chunk
// per page module and both call sites resolve to the same chunks.
const PAGE_LOADERS: Partial<Record<Page, () => Promise<unknown>>> = {
	aboutAndPrivacy: () => import("@/pages/AboutAndPrivacy"),
	analytics: () => import("@/pages/Dashboard"),
	history: () => import("@/pages/History"),
	microphone: () => import("@/pages/Microphone"),
	models: () => import("@/pages/Models"),
	onboarding: () => import("@/pages/Onboarding"),
	// The settings hub and ALL section-page literals resolve to the SAME
	// Settings chunk — one entry covers them.
	settings: () => import("@/pages/Settings"),
	templates: () => import("@/pages/Templates"),
	vocabulary: () => import("@/pages/Vocabulary"),
};

let idlePrefetchStarted = false;

type IdleWindow = Window & {
	requestIdleCallback?: (cb: () => void, opts?: { timeout?: number }) => number;
};

/**
 * Warm every route chunk during browser idle time. Safe to call
 * multiple times — the work happens once per session.
 */
export function prefetchRouteChunks(): void {
	if (idlePrefetchStarted) return;
	idlePrefetchStarted = true;
	const run = () => {
		for (const load of Object.values(PAGE_LOADERS)) {
			load().catch(() => {
				// Swallow: the real navigation's lazy import retries.
			});
		}
	};
	const idleWindow = window as IdleWindow;
	if (typeof idleWindow.requestIdleCallback === "function") {
		// Timeout bounds how long "busy" can starve the prefetch —
		// 3s after mount the app is interactive and chunks are small.
		idleWindow.requestIdleCallback(run, { timeout: 3000 });
	} else {
		setTimeout(run, 1500);
	}
}

/**
 * Prefetch one page's chunk (sidebar hover/focus intent). No-ops for
 * pages without a lazy chunk (home is eager).
 */
export function prefetchPage(page: Page): void {
	PAGE_LOADERS[page]?.().catch(() => {
		// Swallow: the real navigation's lazy import retries.
	});
}
