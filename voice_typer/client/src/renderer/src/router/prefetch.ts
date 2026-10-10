// Route-chunk + data prefetching (vercel-react-best-practices:
// bundle-preload; TanStack prefetchQuery / Next.js hover-prefetch).
// The 10 secondary routes are React.lazy chunks (see PageSwitch.tsx).
// Without prefetching, the FIRST navigation to each page waits on a
// dynamic import before anything renders. This module closes that gap:
//   1. `prefetchRouteChunks()`, called once from App after mount, on
//      `requestIdleCallback`, warms every route chunk. The app is a
//      desktop shell (local files, small chunks), so warming all of
//      them at idle is effectively free and makes every subsequent
//      route switch render synchronously from React.lazy's module
//      cache, no Suspense fallback at all.
//   2. `prefetchPage(page)`, intent-based backup for the idle pass:
//      the Sidebar calls it on nav-item hover/focus so the chunk is
//      already streaming before the click lands. For data-heavy pages
//      (analytics today) it ALSO warms the data cache via a dynamic
//      import, so the click lands on cached data, not a fetch.
// Fire-and-forget: a failed prefetch (dev HMR race, crash) must never
// surface, the normal lazy import path still handles the load.

import type { PrefetchCall } from "@/lib/snapshotCache";
import type { Page } from "@/types/ipc";

import { PAGE_LOADERS, routeChunkLoader } from "./pageLoaders";

let idlePrefetchStarted = false;

type IdleWindow = Window & {
	requestIdleCallback?: (cb: () => void, opts?: { timeout?: number }) => number;
};

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

export function prefetchPage(page: Page): void {
	routeChunkLoader(page)?.().catch(() => {
		// Swallow: the real navigation's lazy import retries.
	});
	prefetchPageData(page);
}

// Data prefetch per page (C-CACHE-4). Dynamic import keeps the page
// query code out of the main chunk; the callee's own TTL + in-flight
// guards make repeated hovers free, and the mount fetch piggybacks an
// in-flight prefetch instead of duplicating it.
function bridgePrefetchCall(): PrefetchCall | null {
	const bridge = (
		window as unknown as {
			python?: {
				call: (req: {
					type: string;
					data?: Record<string, unknown>;
				}) => Promise<unknown>;
			};
		}
	).python;
	if (!bridge) return null;
	return (type: string, data?: Record<string, unknown>) =>
		bridge.call({ type, data });
}

function prefetchPageData(page: Page): void {
	if (page === "analytics") {
		import("@/pages/dashboard/hooks/useDashboardData")
			.then(({ prefetchDashboardData }) => {
				const call = bridgePrefetchCall();
				if (!call) return;
				void prefetchDashboardData(call);
			})
			.catch(() => {
				// Swallow: the mount fetch stays authoritative.
			});
		return;
	}
	if (page === "history") {
		import("@/pages/history/hooks/useHistoryCache")
			.then(({ prefetchHistoryData }) => {
				const call = bridgePrefetchCall();
				if (!call) return;
				void prefetchHistoryData(call);
			})
			.catch(() => {
				// Swallow: the mount fetch stays authoritative.
			});
		return;
	}
	if (page === "vocabulary") {
		import("@/pages/vocabulary/hooks/useVocabulary")
			.then(({ prefetchVocabularyData }) => {
				const call = bridgePrefetchCall();
				if (!call) return;
				void prefetchVocabularyData(call);
			})
			.catch(() => {
				// Swallow: the mount fetch stays authoritative.
			});
		return;
	}
	if (page === "templates") {
		import("@/pages/templates/hooks/useTemplates")
			.then(({ prefetchTemplatesData }) => {
				const call = bridgePrefetchCall();
				if (!call) return;
				void prefetchTemplatesData(call);
			})
			.catch(() => {
				// Swallow: the mount fetch stays authoritative.
			});
		return;
	}
	if (page === "models") {
		// Status-only prefetch (the disk stat is the expensive leg;
		// config + catalog still fetch on mount). The cache module is
		// tiny, no page chunk is pulled in.
		import("@/hooks/models/modelStatusCache")
			.then(({ prefetchModelsStatus }) => {
				const call = bridgePrefetchCall();
				if (!call) return;
				void prefetchModelsStatus(call);
			})
			.catch(() => {
				// Swallow: the mount fetch stays authoritative.
			});
		return;
	}
}
