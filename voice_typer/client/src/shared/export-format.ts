/**
 * Canonical ExportFormat union — shared between main process, preload, and renderer.
 *
 * Extracted from the 12+ inline `"json" | "csv"` declarations that were
 * redeclared at each call site (export-handlers, preload bridge types,
 * ExportFormatMenu, and the per-page toolbar/import-export hooks). Both
 * `tsconfig.web.json` and `tsconfig.node.json` recursively include
 * everything under `src/shared` (their `include` arrays carry the
 * `src/shared` glob), so a single import resolves in either scope.
 *
 * The renderer's export UI (ExportFormatMenu) and the per-page
 * import/export hooks consume this type, as do the preload bridge type
 * declarations and the main-process export IPC handlers. Adding a new
 * format (e.g. `"tsv"`) now requires touching exactly one file.
 *
 * Stability contract: these literal strings are also used as file
 * extensions and as serialized IPC payload fields — never rename an
 * existing member (only add new ones).
 */
export type ExportFormat = "json" | "csv";
