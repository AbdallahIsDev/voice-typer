/**
 * MicrophoneListItem unit tests — the "System Default" badge token.
 *
 * The badge sits on `bg-accent`, and `--accent` maps to `var(--primary)`
 * in every theme block (light, dark, custom presets). Its foreground
 * must therefore be the paired `text-accent-foreground` token: a
 * hardcoded `text-white` is unreadable whenever the accent/primary is a
 * light color (e.g. light theme), while `--accent-foreground` always
 * resolves to the contrast-safe companion of the active accent.
 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MicrophoneListItem } from "@/components/microphone/MicrophoneListItem";
import { RadioGroup } from "@/components/ui/radio-group";
import type { MicrophoneDevice } from "@/types/config";

vi.mock("@/i18n/i18n", () => ({
	t: (key: string) => key,
}));

vi.mock("@hugeicons/react", () => ({
	HugeiconsIcon: ({ icon }: { icon?: { name?: string } }) => (
		<span data-testid="hugeicon" data-name={icon?.name} aria-hidden />
	),
}));

function renderRow(mic: Partial<MicrophoneDevice>, checked = false) {
	// Radix RadioGroupItem requires a Root ancestor (production always
	// renders rows inside the list's single RadioGroup).
	return render(
		<RadioGroup>
			<MicrophoneListItem
				mic={{ id: "default", name: "Test Mic", ...mic } as MicrophoneDevice}
				checked={checked}
				showDefaultBadge={!checked}
				disabled={false}
				onSelect={() => {}}
			/>
		</RadioGroup>,
	);
}

describe("MicrophoneListItem OS-default badge uses the accent foreground token", () => {
	afterEach(() => {
		cleanup();
	});

	it("renders the badge with text-accent-foreground (never hardcoded text-white)", () => {
		renderRow({ default: true });
		const badge = screen.getByText("microphone.osDefaultBadge");
		expect(badge.className).toContain("bg-accent");
		expect(badge.className).toContain("text-accent-foreground");
		expect(badge.className).not.toContain("text-white");
	});

	it("does not render the badge when the OS default IS the active selection", () => {
		// checked=true → the parent suppresses showDefaultBadge (the
		// selected System Default row already communicates the state).
		renderRow({ default: true }, true);
		expect(screen.queryByText("microphone.osDefaultBadge")).toBeNull();
	});
});
