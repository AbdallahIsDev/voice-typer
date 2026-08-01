import path from "node:path";
import { fileURLToPath } from "node:url";

//single source of truth for the renderer-side path aliases
// (`@`, `#ui`, `#utils`) used by every Vite config in the client root.
//
// Previously `electron.vite.config.ts`, `electron.vite.renderer.ts`, and
// `vitest.config.ts` each duplicated the same three `resolve(__dirname,
// "src/renderer/src/...")` calls — a maintenance hazard where a path
// change had to be applied in 3 places. Centralising here means a path
// change is a single-line edit; tsc flags every config that imports
// `aliases` if the exported shape ever drifts.
//
// `vite.config.ts` is intentionally NOT migrated — shadcn CLI requires
// a Vite-shaped config with inline `resolve.alias` literals (see the
// comment at the top of `vite.config.ts`).
const __dirname = path.dirname(fileURLToPath(import.meta.url));

export const aliases = {
	"@": path.resolve(__dirname, "src/renderer/src"),
	"#ui": path.resolve(__dirname, "src/renderer/src/components/ui"),
	"#utils": path.resolve(__dirname, "src/renderer/src/lib/utils.ts"),
} as const;
