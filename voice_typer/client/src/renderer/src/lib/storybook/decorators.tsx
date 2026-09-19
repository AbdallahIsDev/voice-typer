import type { Decorator } from "@storybook/react";

export function themeVariantDecorator(options: {
	dark?: boolean;
	rtl?: boolean;
}): Decorator {
	return (Story) => (
		<div
			dir={options.rtl ? "rtl" : undefined}
			lang={options.rtl ? "ar" : undefined}
			className={`bg-background p-6 text-(--text-primary) ${
				options.dark ? "dark" : ""
			}`}
		>
			<Story />
		</div>
	);
}
