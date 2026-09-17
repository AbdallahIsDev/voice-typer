import { resolve } from "node:path";
import { defineConfig } from "vite";

// shadcn CLI's framework detection globs for files matching `vite.config.*`
// at the project root. This file is a minimal Vite-shaped config so
// shadcn sees a Vite project. The production/dev Tauri builds use
// `vite.tauri.config.ts` (multi-page + outDir), not this file.
export default defineConfig({
	root: resolve(__dirname, "src/renderer"),
	resolve: {
		alias: {
			"@": resolve(__dirname, "src/renderer/src"),
			// @server removed, resolved outside renderer root and
			// crashed Vite HMR on locale switch. The JSON copy is
			// imported with a project-relative path.
			//removed non-existent barrel file aliases
			"#ui": resolve(__dirname, "src/renderer/src/components/ui"),
			"#utils": resolve(__dirname, "src/renderer/src/lib/utils.ts"),
		},
	},
});
