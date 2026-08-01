// src/renderer/src/lib/tauri-bridge/install.ts
//
//fix: isolated side-effect module.
//
// `index.ts` exports `installTauriBridge` (named) and the namespace
// factories but NO LONGER auto-invokes it at import time. Tests and
// downstream modules that import `@/lib/tauri-bridge` for its named
// exports (e.g. `createPythonNamespace`, `isTauri`, `makeListener`)
// no longer trigger a side effect that mutates `window.python`,
// `window.bubble`, `window.window_`.
//
// Production entrypoints (`main.tsx`, `bubble-main.tsx`) import this
// module explicitly for the side effect:
//   import "@/lib/tauri-bridge/install";
//
// Tests that exercise the auto-install behavior import this module
// instead of `@/lib/tauri-bridge` so the side effect runs under
// `vi.resetModules()` isolation as expected.

import { installTauriBridge } from "./index";

// Auto-install when this module is imported. Both `main.tsx` (main
// window) and `bubble-main.tsx` (bubble window) import this module at
// the top so the bridge is ready before the React app mounts. In
// Electron mode `installTauriBridge` is a no-op (preload already
// installed the namespaces).
installTauriBridge();

export { installTauriBridge };
