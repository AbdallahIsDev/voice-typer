import type { Preview } from "@storybook/react";
import "../src/renderer/src/index.css";

// Storybook 9 merged `@storybook/addon-essentials` into core, so the
// controls/viewport/docs matchers are configured here directly.
const preview: Preview = {
	parameters: {
		controls: {
			matchers: {
				color: /(background|color)$/i,
				date: /Date$/i,
			},
		},
	},
};

export default preview;
