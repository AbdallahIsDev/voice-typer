/**
 * AnalyticsRangeSwitcher — the Analytics time range in the title bar.
 *
 * The control is the only way to change the range, so what matters is:
 * it mounts on the analytics page and nowhere else, it exposes every
 * range as a labelled radio, it writes through to the shared store (the
 * page reads that same store, which is the whole reason the control can
 * live outside the page's tree), and it follows the store when the
 * value changes from elsewhere.
 */

import {
	act,
	cleanup,
	fireEvent,
	render,
	screen,
	waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AnalyticsRangeSwitcher } from "@/components/layout/AnalyticsRangeSwitcher";
import { t } from "@/i18n/i18n";
import {
	ANALYTICS_RANGES,
	useAnalyticsRange,
} from "@/stores/useAnalyticsRange";
import type { Page } from "@/types/ipc";

beforeEach(() => {
	sessionStorage.clear();
	useAnalyticsRange.setState({ range: "7d", customWindow: null });
});

afterEach(() => {
	cleanup();
	useAnalyticsRange.setState({ range: "7d", customWindow: null });
});

describe("AnalyticsRangeSwitcher", () => {
	it("renders nothing on every page except analytics", () => {
		const otherPages: Page[] = ["home", "history", "models", "settings"];
		for (const page of otherPages) {
			const { container, unmount } = render(
				<AnalyticsRangeSwitcher currentPage={page} />,
			);
			expect(container.firstChild).toBeNull();
			unmount();
		}
	});

	it("exposes every range as a labelled option on the analytics page", () => {
		render(<AnalyticsRangeSwitcher currentPage="analytics" />);

		const group = screen.getByRole("radiogroup", {
			name: t("analytics.rangeAria"),
		});
		// The strip sits inside the title bar's drag region: without
		// `no-drag` a click would move the window instead of picking a
		// range.
		expect(group.className).toContain("no-drag");

		const radios = screen.getAllByRole("radio");
		// Four presets + the icon-only Custom item (covered in detail
		// by the custom-entry suite below).
		expect(radios).toHaveLength(ANALYTICS_RANGES.length + 1);
		for (const range of ANALYTICS_RANGES) {
			expect(
				screen.getByRole("radio", { name: t(`analytics.range.${range}`) }),
			).toBeTruthy();
		}
		// Radiogroup, NOT the tabs variant: the range picks a data
		// window and has no `role="tabpanel"` to control.
		expect(screen.queryByRole("tablist")).toBeNull();
	});

	it("writes the selection through to the shared store", () => {
		render(<AnalyticsRangeSwitcher currentPage="analytics" />);

		fireEvent.click(
			screen.getByRole("radio", { name: t("analytics.range.30d") }),
		);

		expect(useAnalyticsRange.getState().range).toBe("30d");
		expect(
			screen.getByRole("radio", { name: t("analytics.range.30d") }),
		).toBeChecked();
	});

	it("follows the store when the range changes from elsewhere", () => {
		render(<AnalyticsRangeSwitcher currentPage="analytics" />);

		act(() => {
			useAnalyticsRange.getState().setRange("all");
		});

		expect(
			screen.getByRole("radio", { name: t("analytics.range.all") }),
		).toBeChecked();
	});
});

describe("AnalyticsRangeSwitcher custom entry", () => {
	it("renders Custom as an icon-only fifth item inside the switch", () => {
		render(<AnalyticsRangeSwitcher currentPage="analytics" />);

		// Four preset radios + the Custom icon radio (accessible name
		// from its title; the primitive renders icon items textless).
		expect(screen.getAllByRole("radio")).toHaveLength(5);
		expect(
			screen.getByRole("radio", { name: t("analytics.range.custom") }),
		).toBeTruthy();
	});

	it("clicking Custom opens a single-month panel with no preset rail", () => {
		render(<AnalyticsRangeSwitcher currentPage="analytics" />);

		fireEvent.click(
			screen.getByRole("radio", { name: t("analytics.range.custom") }),
		);

		// Panel dialog present; the rail (outer pills own those presets)
		// and the footer actions (dates auto-apply) are gone.
		expect(
			screen.getByRole("dialog", { name: t("analytics.rangeAria") }),
		).toBeTruthy();
		expect(
			screen.queryByRole("button", {
				name: t("analytics.rangePicker.apply"),
			}),
		).toBeNull();
		// Pills stay mounted behind the panel.
		expect(screen.getAllByRole("radio")).toHaveLength(5);
	});

	it("marks Custom checked (old preset released) while the picker is open", () => {
		render(<AnalyticsRangeSwitcher currentPage="analytics" />);

		const custom = screen.getByRole("radio", {
			name: t("analytics.range.custom"),
		});
		const preset = screen.getByRole("radio", {
			name: t("analytics.range.7d"),
		});
		expect(preset).toBeChecked();

		fireEvent.click(custom);

		// Pending selection reads Custom: the icon goes full-opacity
		// and the preset mutes, instead of stranding a muted icon
		// beside a still-white preset.
		expect(custom).toBeChecked();
		expect(preset).not.toBeChecked();
		const iconClass =
			custom.closest("label")?.querySelector("svg")?.getAttribute("class") ??
			"";
		expect(iconClass).toMatch(/opacity-100/);
		expect(iconClass).not.toMatch(/opacity-60/);
	});

	it("cancelling the picker hands the row back to the stored preset", async () => {
		render(<AnalyticsRangeSwitcher currentPage="analytics" />);

		const custom = screen.getByRole("radio", {
			name: t("analytics.range.custom"),
		});
		fireEvent.click(custom);
		expect(
			screen.getByRole("dialog", { name: t("analytics.rangeAria") }),
		).toBeTruthy();

		fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });

		// The exit morph holds the panel briefly; it must unmount.
		await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
		expect(
			screen.getByRole("radio", { name: t("analytics.range.7d") }),
		).toBeChecked();
		expect(custom).not.toBeChecked();
	});

	it("picking a preset while the picker is open closes it and applies", async () => {
		render(<AnalyticsRangeSwitcher currentPage="analytics" />);

		fireEvent.click(
			screen.getByRole("radio", { name: t("analytics.range.custom") }),
		);
		expect(
			screen.getByRole("dialog", { name: t("analytics.rangeAria") }),
		).toBeTruthy();

		fireEvent.click(
			screen.getByRole("radio", { name: t("analytics.range.30d") }),
		);

		expect(useAnalyticsRange.getState().range).toBe("30d");
		// The exit morph holds the panel briefly; it must unmount.
		await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
	});

	it("the Custom icon lifts on hover and rests muted (group-hover contract)", () => {
		render(<AnalyticsRangeSwitcher currentPage="analytics" />);

		const custom = screen.getByRole("radio", {
			name: t("analytics.range.custom"),
		});
		const label = custom.closest("label");
		expect(label?.className).toMatch(/\bgroup\b/);
		const iconClass = label?.querySelector("svg")?.getAttribute("class") ?? "";
		expect(iconClass).toMatch(/opacity-60/);
		expect(iconClass).toMatch(/group-hover:opacity-100/);
	});

	it("hangs the panel from an anchor directly below the pill row", () => {
		render(<AnalyticsRangeSwitcher currentPage="analytics" />);

		fireEvent.click(
			screen.getByRole("radio", { name: t("analytics.range.custom") }),
		);
		const dialog = screen.getByRole("dialog", {
			name: t("analytics.rangeAria"),
		});

		// Trigger-less: the panel IS the floating container, with no
		// surface wrapper between it and the picker's root.
		const root = dialog.parentElement;
		expect(root?.getAttribute("class") ?? "").toContain("_root");
		expect(root?.getAttribute("class") ?? "").not.toContain("surface");

		// The anchor is a PLAIN div, so its positioning utilities actually
		// apply. The picker's own root is `position: relative` from its
		// stylesheet, which silently beats a layered `absolute` utility —
		// the old markup put these classes on the root and the panel
		// ended up hanging off the end of the row instead of under it.
		const anchor = root?.parentElement;
		const anchorClass = anchor?.getAttribute("class") ?? "";
		expect(anchorClass).toContain("absolute");
		expect(anchorClass).toContain("right-0");
		expect(anchorClass).toContain("top-[calc(100%+8px)]");
		// ...and the row is what it is positioned against.
		expect(anchor?.parentElement?.getAttribute("class") ?? "").toContain(
			"relative",
		);

		// align="end": the panel grows leftward from the anchor, so its
		// right edge stays under the Custom pill rather than opening off
		// the right of the window.
		expect(dialog.getAttribute("style") ?? "").toContain("top right");
	});

	it("re-clicking the Custom pill while open leaves the panel and the row alone", () => {
		render(<AnalyticsRangeSwitcher currentPage="analytics" />);

		const custom = screen.getByRole("radio", {
			name: t("analytics.range.custom"),
		});
		fireEvent.click(custom);
		expect(
			screen.getByRole("dialog", { name: t("analytics.rangeAria") }),
		).toBeTruthy();

		// A click is a pointerdown THEN a click. The pointerdown lands on
		// the pill row, which the picker's dismissGuard reports as INSIDE.
		// Without that the panel dismisses here and the click reopens it,
		// so the active indicator animates off the pill and back — the
		// glitch this guards.
		fireEvent.pointerDown(custom.closest("label") as HTMLElement);
		expect(
			screen.queryByRole("dialog", { name: t("analytics.rangeAria") }),
		).toBeTruthy();
		expect(custom).toBeChecked();

		// The click itself is idempotent: still open, still on Custom.
		fireEvent.click(custom);
		expect(
			screen.queryByRole("dialog", { name: t("analytics.rangeAria") }),
		).toBeTruthy();
		expect(custom).toBeChecked();
	});

	// Once a custom range is committed the Custom pill IS the active radio
	// value, and a radio ignores a re-click of itself — so `onChange` never
	// fires and, without the primitive's `onOptionActivate`, the panel could
	// never be reopened. This is the regression guard for that.
	it("reopens the panel when a custom range is already committed", () => {
		useAnalyticsRange.setState({
			range: "custom",
			customWindow: { startKey: "2026-09-01", endKey: "2026-09-03" },
		});
		render(<AnalyticsRangeSwitcher currentPage="analytics" />);

		const custom = screen.getByRole("radio", {
			name: t("analytics.range.custom"),
		});
		expect(custom).toBeChecked();
		expect(
			screen.queryByRole("dialog", { name: t("analytics.rangeAria") }),
		).toBeNull();

		fireEvent.click(custom);

		expect(
			screen.getByRole("dialog", { name: t("analytics.rangeAria") }),
		).toBeTruthy();
		// Still Custom: reopening must not move the selection.
		expect(custom).toBeChecked();
		expect(useAnalyticsRange.getState().range).toBe("custom");
	});

	it("outside dismiss unmounts the panel and leaves no hollow box", async () => {
		render(<AnalyticsRangeSwitcher currentPage="analytics" />);

		fireEvent.click(
			screen.getByRole("radio", { name: t("analytics.range.custom") }),
		);
		const dialog = screen.getByRole("dialog", {
			name: t("analytics.rangeAria"),
		});
		// Trigger-less: nothing sizes a surface, so there is no wrapper to
		// strand at the panel's old size once it closes.
		const root = dialog.parentElement;
		expect(root?.getAttribute("class") ?? "").not.toContain("surface");

		fireEvent.pointerDown(document.body);

		await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
		expect(
			screen.getByRole("radio", { name: t("analytics.range.7d") }),
		).toBeChecked();
	});

	it("picking two days auto-applies a custom window, pills stay", () => {
		// Pin "today" so the picked days are deterministic (past,
		// same-month, inside min/max bounds).
		vi.useFakeTimers();
		vi.setSystemTime(new Date(2026, 9, 15, 12, 0, 0));
		try {
			render(<AnalyticsRangeSwitcher currentPage="analytics" />);
			fireEvent.click(
				screen.getByRole("radio", { name: t("analytics.range.custom") }),
			);

			const dayName = (day: number) =>
				new Intl.DateTimeFormat("en", {
					weekday: "long",
					month: "long",
					day: "numeric",
					year: "numeric",
				}).format(new Date(2026, 9, day));
			fireEvent.click(screen.getByRole("button", { name: dayName(10) }));
			fireEvent.click(screen.getByRole("button", { name: dayName(12) }));

			expect(useAnalyticsRange.getState().range).toBe("custom");
			expect(useAnalyticsRange.getState().customWindow).toEqual({
				startKey: "2026-10-10",
				endKey: "2026-10-12",
			});
			// Panel closed itself; the pill row (Custom highlighted) is
			// untouched.
			expect(screen.getAllByRole("radio")).toHaveLength(5);
			expect(
				screen.getByRole("radio", { name: t("analytics.range.custom") }),
			).toBeChecked();
		} finally {
			vi.useRealTimers();
		}
	});
});
