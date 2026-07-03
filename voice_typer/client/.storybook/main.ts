import { resolve } from "node:path";
import type { StorybookConfig } from "@storybook/react-vite";

// Storybook 9 configuration for the voice-typer Electron renderer.
//
// Note: `@storybook/addon-essentials` was merged into the `storybook`
// core package in v9 (controls, actions, docs, viewport, highlight all
// ship with the core). Only `@storybook/addon-themes` is listed
// explicitly here so the light/dark theme switcher renders in the
// Storybook toolbar.
//
// `viteFinal` injects the same path aliases (`@`, `#ui`, `#utils`)
// that `vite.config.ts` and `electron.vite.config.ts` use for the
// renderer so existing components can be imported unchanged.
const config: StorybookConfig = {
	stories: ["../src/renderer/src/**/*.stories.@(ts|tsx|mdx)"],
	addons: ["@storybook/addon-themes"],
	framework: {
		name: "@storybook/react-vite",
		options: {},
	},
	viteFinal: async (cfg) => {
		cfg.resolve ??= {};
		const existingAliases =
			typeof cfg.resolve.alias === "object" && cfg.resolve.alias !== null
				? (cfg.resolve.alias as Record<string, string>)
				: {};
		cfg.resolve.alias = {
			...existingAliases,
			"@": resolve(__dirname, "../src/renderer/src"),
			"#ui": resolve(__dirname, "../src/renderer/src/components/ui"),
			"#utils": resolve(__dirname, "../src/renderer/src/lib/utils.ts"),
		};
		// The project's vite.config.ts sets `root` to src/renderer which
		// would prevent Storybook from discovering stories/.stories.tsx
		// files. Reset it to the project root.
		cfg.root = resolve(__dirname, "..");
		return cfg;
	},
};

export default config;
