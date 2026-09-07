// router/pageLoaders.ts — the SINGLE lazy-import registry for the
// secondary route chunks.
//
// Previously the registry was maintained TWICE: PageSwitch.tsx
// declared nine `lazy(() => import("@/pages/X"))` components while
// prefetch.ts kept its own nine-entry `PAGE_LOADERS` map of the SAME
// import specifiers — the two lists could (and did) drift silently.
// This module is now the one source of truth:
//
//   - `PAGE_LOADERS` — the raw chunk loaders (one `import()` per
//     secondary page). `router/prefetch.ts` warms these at idle and
//     on sidebar hover.
//   - `LAZY_PAGES` — the `React.lazy` components created ONCE at
//     module scope from the same loaders. `router/PageSwitch.tsx`
//     renders these; because React.lazy latches the resolved module,
//     a chunk warmed via `PAGE_LOADERS` and later rendered via
//     `LAZY_PAGES` resolves to the SAME module instance (Vite's
//     dynamic-import cache deduplicates by specifier).
//
// Home stays an EAGER import in PageSwitch.tsx (default landing page —
// lazy-loading it would put a Suspense fallback flash on every app
// launch), so it deliberately has no entry here.
//
// The nine entries are the pages with their own route chunk. The
// Settings HUB and ALL settings-section literals resolve to the SAME
// Settings chunk (one entry covers them); `routeChunkLoader` returns
// `undefined` for pages without their own chunk so `prefetchPage`
// can no-op for them.
//
// Adding a page: add the literal to the `Page` union in
// `types/ipc.ts`, add a `RouteDef` to `router/routes.ts`, add an
// entry here (if the page gets its own chunk), and add a `case` to
// `PageSwitch`'s switch (component wiring — legitimate routing
// logic, not registry duplication).

import { lazy } from "react";
import type { Page } from "@/types/ipc";

/**
 * The lazy chunk loaders — the canonical registry of dynamic imports
 * for the secondary routes. Each entry resolves to the page module's
 * default export.
 */
export const PAGE_LOADERS = {
	aboutAndPrivacy: () => import("@/pages/AboutAndPrivacy"),
	analytics: () => import("@/pages/Dashboard"),
	history: () => import("@/pages/History"),
	microphone: () => import("@/pages/Microphone"),
	models: () => import("@/pages/Models"),
	onboarding: () => import("@/pages/Onboarding"),
	// The settings hub and ALL section-page literals resolve to the
	// SAME Settings chunk — one entry covers them.
	settings: () => import("@/pages/Settings"),
	templates: () => import("@/pages/Templates"),
	vocabulary: () => import("@/pages/Vocabulary"),
} as const;

/** Pages that own a lazy route chunk in {@link PAGE_LOADERS}. */
export type PageWithLazyChunk = keyof typeof PAGE_LOADERS;

/**
 * The `React.lazy` components for the secondary routes, created ONCE
 * at module scope from the {@link PAGE_LOADERS} entries. Rendered by
 * `router/PageSwitch.tsx` behind its Suspense boundary.
 */
export const LAZY_PAGES = {
	aboutAndPrivacy: lazy(PAGE_LOADERS.aboutAndPrivacy),
	analytics: lazy(PAGE_LOADERS.analytics),
	history: lazy(PAGE_LOADERS.history),
	microphone: lazy(PAGE_LOADERS.microphone),
	models: lazy(PAGE_LOADERS.models),
	onboarding: lazy(PAGE_LOADERS.onboarding),
	settings: lazy(PAGE_LOADERS.settings),
	templates: lazy(PAGE_LOADERS.templates),
	vocabulary: lazy(PAGE_LOADERS.vocabulary),
};

/**
 * Loader lookup for prefetching by `Page` value. Returns `undefined`
 * for pages without their own lazy chunk (Home is eager; the
 * settings-section literals share the `settings` chunk, which the
 * `settings` entry covers).
 */
export function routeChunkLoader(
	page: Page,
): (() => Promise<unknown>) | undefined {
	const loaders = PAGE_LOADERS as Partial<Record<Page, () => Promise<unknown>>>;
	const direct = loaders[page];
	if (direct) return direct;
	// Every settings-section literal renders the shared Settings chunk
	// (see PageSwitch), so hover prefetch warms it for those too.
	if (page.startsWith("settings")) return PAGE_LOADERS.settings;
	return undefined;
}
