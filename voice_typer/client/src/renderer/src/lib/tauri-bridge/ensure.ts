// src/renderer/src/lib/tauri-bridge/ensure.ts
//
// The single production gate for installing the Tauri bridge before
// React mounts. Previously this gate (and its ~30-line rationale
// comment) was duplicated — comment and all — in BOTH renderer
// entrypoints (`main.tsx`, `bubble-main.tsx`); it lives here now so
// the contract has one home.
//
// ADR-0020 §6.3 (Phase 3 UI port): the bridge namespaces
// (`window.python` / `window.bubble` / `window.window_`) must exist
// BEFORE the React app mounts so `usePython` and other hooks
// initialize against a live bridge. Under Electron the preload script
// (`src/preload/index.ts`) already installs the namespaces via
// `contextBridge.exposeInMainWorld`, so the install is a no-op there.
//
// The side-effect module is `./install` (the sibling `install.ts`,
// which auto-invokes `installTauriBridge()` at module load). The
// import below is a RUNTIME-GATED DYNAMIC import, NOT a static
// top-level import: a static `import "./lib/tauri-bridge/install"`
// would pull the whole install graph into the eagerly-loaded renderer
// bundle even under Electron, where it is never needed. Gated on
// `isTauri()` (`./detect.ts` — the `window.__TAURI__?.core?.invoke`
// check), the bundler emits `install.ts` as a SEPARATE async chunk
// that is fetched ONLY when the renderer actually runs inside a
// Tauri WebView. Under Electron the gate is false and the chunk is
// never fetched.
//
// This module MUST stay dependency-light: it may import `./detect`
// (pure — no `window` mutation, no Tauri API surface) and NOTHING
// else from the bridge. That is why `ensureTauriBridgeInstalled`
// lives here and not in `install.ts` itself: statically importing a
// function from `install.ts` would drag `install.ts` (and its import
// graph) back into the entrypoints' eager bundle — exactly the
// regression the runtime gate exists to prevent.
//
// Callers use top-level await, which guarantees ordering: the
// `ReactDOM.createRoot().render(...)` call at the bottom of each
// entrypoint does not execute until the bridge is installed (Vite
// supports top-level await in ESM modules; the `<script
// type="module">` tag defers subsequent module scripts until this
// one resolves).

import { isTauri } from "./detect";

/**
 * Ensure the Tauri bridge is installed before the caller's React tree
 * mounts. No-op under Electron (the preload script owns the
 * namespaces there); under Tauri it fetches the install chunk and
 * runs `installTauriBridge()` before resolving. Safe to call from
 * both entrypoints — `installTauriBridge()` is idempotent.
 */
export async function ensureTauriBridgeInstalled(): Promise<void> {
	if (isTauri()) {
		await import("./install");
	}
}
