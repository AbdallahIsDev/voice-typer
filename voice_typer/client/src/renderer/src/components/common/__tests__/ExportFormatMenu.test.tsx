/**
 *  / A11Y-7 / SET-2: Keyboard accessibility tests for ExportFormatMenu.
 *
 * The pre-fix menu was mouse-only: no arrow-key navigation, no Escape-to-
 * close, no Home/End jumps, and no aria-controls linking the trigger
 * button to the menu. After the fix:
 *   - Trigger button has `aria-controls` pointing at the menu id when
 *     open, so AT can announce the trigger↔menu relationship.
 *   - Menu auto-focuses the first menuitem when opened (no extra Tab
 *     needed).
 *   - ArrowDown/ArrowUp move focus between menuitems (roving tabindex).
 *   - Home/End jump to the first/last menuitem.
 *   - Escape closes the menu and returns focus to the trigger button.
 *   - Tab closes the menu (lets natural focus order continue).
 *
 * Tests use @testing-library/user-event because it simulates the full
 * keyboard event pipeline (keydown → keypress → keyup → input) and
 * triggers React's synthetic event handlers the same way a real user
 * keypress does. Plain fireEvent.keyDown would bypass some of these
 * steps and give an unrealistic pass.
 */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import ExportFormatMenu from "@/components/common/ExportFormatMenu";

// Mock the hugeicons runtime wrapper so we don't pull in the real SVG
// renderer (heavy + browser-only). The mock renders a stub <span> that
// surfaces the icon name via data-name so tests can assert on it if
// needed.
vi.mock("@hugeicons/react", () => ({
	HugeiconsIcon: ({
		children,
		icon,
	}: {
		children?: React.ReactNode;
		icon?: { name?: string };
	}) => (
		<span data-testid="hugeicon" data-name={icon?.name}>
			{children}
		</span>
	),
}));

vi.mock("@hugeicons/core-free-icons", () => {
	const make = (name: string) => ({ name });
	return { Download01Icon: make("Download01Icon") };
});

async function openMenu(user: ReturnType<typeof userEvent.setup>) {
	const trigger = screen.getByRole("button", { name: /export/i });
	await user.click(trigger);
	// Wait for the menu to be present so subsequent assertions don't
	// race the React commit.
	await screen.findByRole("menu");
	return trigger;
}

describe("ExportFormatMenu — A11Y-7 / SET-2 (keyboard nav)", () => {
	afterEach(() => {
		cleanup();
	});

	it("trigger button has aria-haspopup=menu and aria-expanded flips when toggled", async () => {
		const user = userEvent.setup();
		render(<ExportFormatMenu onExport={vi.fn()} />);

		const trigger = screen.getByRole("button", { name: /export/i });
		expect(trigger).toHaveAttribute("aria-haspopup", "menu");
		expect(trigger).toHaveAttribute("aria-expanded", "false");

		await user.click(trigger);
		expect(trigger).toHaveAttribute("aria-expanded", "true");
	});

	it("trigger has aria-controls linking to the menu when open (and omits it when closed)", async () => {
		const user = userEvent.setup();
		render(<ExportFormatMenu onExport={vi.fn()} />);

		const trigger = screen.getByRole("button", { name: /export/i });
		// Closed initially: aria-controls should be undefined (or absent).
		// React renders `aria-controls={undefined}` as no attribute.
		expect(trigger).not.toHaveAttribute("aria-controls");

		await user.click(trigger);
		const menu = await screen.findByRole("menu");
		// aria-controls on the trigger must point at the menu's id.
		const controlsId = trigger.getAttribute("aria-controls");
		expect(controlsId).toBeTruthy();
		expect(menu.id).toBe(controlsId);
	});

	it("menu auto-focuses the first menuitem when opened (no Tab needed)", async () => {
		const user = userEvent.setup();
		render(<ExportFormatMenu onExport={vi.fn()} />);

		const trigger = screen.getByRole("button", { name: /export/i });
		await user.click(trigger);
		await screen.findByRole("menu");

		// requestAnimationFrame defers focus, so wait for it via findBy
		// + waitFor. Radix schedules focus on the first menuitem via rAF;
		// in jsdom the rAF callback needs an explicit flush.
		const firstItem = await screen.findByRole("menuitem", {
			name: /export as json/i,
		});
		await waitFor(() => expect(firstItem).toHaveFocus());
	});

	it("ArrowDown moves focus to the second menuitem; ArrowUp moves back", async () => {
		const user = userEvent.setup();
		render(<ExportFormatMenu onExport={vi.fn()} />);

		await openMenu(user);
		const items = await screen.findAllByRole("menuitem");
		expect(items).toHaveLength(2);

		// First item focused after open (Radix defers via rAF).
		await screen.findByRole("menuitem", { name: /export as json/i });
		await waitFor(() => expect(items[0]).toHaveFocus());

		// ArrowDown → second item.
		await user.keyboard("{ArrowDown}");
		await waitFor(() => expect(items[1]).toHaveFocus());

		// ArrowDown again wraps to first item (cyclic nav).
		await user.keyboard("{ArrowDown}");
		await waitFor(() => expect(items[0]).toHaveFocus());

		// ArrowUp wraps back to last item.
		await user.keyboard("{ArrowUp}");
		await waitFor(() => expect(items[1]).toHaveFocus());

		// ArrowUp back to first item.
		await user.keyboard("{ArrowUp}");
		await waitFor(() => expect(items[0]).toHaveFocus());
	});

	it("Home jumps to the first menuitem; End jumps to the last", async () => {
		const user = userEvent.setup();
		render(<ExportFormatMenu onExport={vi.fn()} />);

		await openMenu(user);
		const items = await screen.findAllByRole("menuitem");

		// End → last item.
		await user.keyboard("{End}");
		await waitFor(() => expect(items[1]).toHaveFocus());

		// Home → first item.
		await user.keyboard("{Home}");
		await waitFor(() => expect(items[0]).toHaveFocus());
	});

	it("Escape closes the menu and returns focus to the trigger button", async () => {
		const user = userEvent.setup();
		render(<ExportFormatMenu onExport={vi.fn()} />);

		const trigger = await openMenu(user);
		// Confirm a menuitem is focused before Escape.
		const firstItem = await screen.findByRole("menuitem", {
			name: /export as json/i,
		});
		await waitFor(() => expect(firstItem).toHaveFocus());

		await user.keyboard("{Escape}");

		// Menu is gone (wait for React to flush the state update triggered
		// by the Escape keydown handler — user.keyboard awaits the event
		// but the React re-render may need one more microtask).
		await screen.findByRole("button", { name: /export/i });
		expect(screen.queryByRole("menu")).toBeNull();
		// Trigger regained focus (Radix restores focus asynchronously).
		await waitFor(() => expect(trigger).toHaveFocus());
		// aria-expanded reflects the closed state.
		expect(trigger).toHaveAttribute("aria-expanded", "false");
	});

	it("Tab closes the menu (focus naturally moves onward)", async () => {
		const user = userEvent.setup();
		render(<ExportFormatMenu onExport={vi.fn()} />);

		await openMenu(user);
		await screen.findByRole("menuitem", { name: /export as json/i });

		// Tab should close the menu. We don't preventDefault, so focus
		// will move to the next focusable element in document order (there
		// isn't one in this isolated render, so focus lands on <body>).
		await user.tab();

		expect(screen.queryByRole("menu")).toBeNull();
	});

	it("clicking a menuitem calls onExport with the right format and closes the menu", async () => {
		const user = userEvent.setup();
		const onExport = vi.fn();
		render(<ExportFormatMenu onExport={onExport} />);

		await openMenu(user);
		const jsonItem = await screen.findByRole("menuitem", {
			name: /export as json/i,
		});
		await user.click(jsonItem);

		expect(onExport).toHaveBeenCalledTimes(1);
		expect(onExport).toHaveBeenCalledWith("json");
		expect(screen.queryByRole("menu")).toBeNull();
	});

	it("does not open the menu when disabled", async () => {
		const user = userEvent.setup();
		render(<ExportFormatMenu onExport={vi.fn()} disabled />);

		const trigger = screen.getByRole("button", { name: /export/i });
		// A disabled button ignores pointer events entirely.
		await user.click(trigger).catch(() => {
			// userEvent rejects clicks on disabled elements; treat as no-op.
		});

		expect(trigger).toBeDisabled();
		expect(screen.queryByRole("menu")).toBeNull();
	});
});
