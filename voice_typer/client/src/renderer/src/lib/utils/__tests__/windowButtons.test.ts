import { describe, expect, it } from "vitest";
import {
	DEFAULT_LINUX_WINDOW_BUTTONS,
	resolveLinuxWindowButtons,
} from "@/lib/utils/windowButtons";
import type {
	LinuxWindowButtonsConfig,
	LinuxWindowButtonsSystemInfo,
} from "@/types/config";

const SYSTEM: LinuxWindowButtonsSystemInfo = {
	desktop_environment: "gnome",
	layout: { side: "left", buttons: ["close", "minimize"] },
};

const CUSTOM: LinuxWindowButtonsConfig = {
	mode: "custom",
	side: "left",
	show_minimize: true,
	show_maximize: false,
	show_close: true,
};

describe("resolveLinuxWindowButtons", () => {
	it("defaults (no args) to the classic right-side trio, circle style", () => {
		const r = resolveLinuxWindowButtons();
		expect(r).toEqual({
			side: "right",
			showMinimize: true,
			showMaximize: true,
			showClose: true,
			buttonStyle: "circle",
			followsSystem: false,
		});
	});

	it("DEFAULT_LINUX_WINDOW_BUTTONS mirrors the server dataclass default", () => {
		expect(DEFAULT_LINUX_WINDOW_BUTTONS).toEqual({
			mode: "system",
			side: "right",
			show_minimize: true,
			show_maximize: true,
			show_close: true,
		});
	});

	it("custom mode uses the user's side + visibility flags", () => {
		const r = resolveLinuxWindowButtons(CUSTOM, SYSTEM);
		expect(r.side).toBe("left");
		expect(r.showMinimize).toBe(true);
		expect(r.showMaximize).toBe(false);
		expect(r.showClose).toBe(true);
		expect(r.followsSystem).toBe(false);
	});

	it("system mode uses the desktop's button-layout when available", () => {
		const r = resolveLinuxWindowButtons({ mode: "system" }, SYSTEM);
		expect(r.side).toBe("left");
		expect(r.showClose).toBe(true);
		expect(r.showMinimize).toBe(true);
		expect(r.showMaximize).toBe(false);
		expect(r.followsSystem).toBe(true);
	});

	it("system mode falls back to the right trio when the snapshot is missing", () => {
		const r = resolveLinuxWindowButtons({ mode: "system" }, null);
		expect(r.side).toBe("right");
		expect(r.showMinimize).toBe(true);
		expect(r.showMaximize).toBe(true);
		expect(r.showClose).toBe(true);
		expect(r.followsSystem).toBe(false);
	});

	it("system mode falls back when the probe failed (layout null)", () => {
		const r = resolveLinuxWindowButtons(
			{ mode: "system" },
			{
				desktop_environment: "unknown",
				layout: null,
			},
		);
		expect(r.followsSystem).toBe(false);
		expect(r.side).toBe("right");
	});

	it("KDE sessions get Breeze-style squares, others circles", () => {
		expect(
			resolveLinuxWindowButtons(undefined, {
				desktop_environment: "kde",
				layout: null,
			}).buttonStyle,
		).toBe("square");
		expect(
			resolveLinuxWindowButtons(undefined, {
				desktop_environment: "gnome",
				layout: null,
			}).buttonStyle,
		).toBe("circle");
		// No snapshot at all → circle (the GNOME-ish default).
		expect(resolveLinuxWindowButtons().buttonStyle).toBe("circle");
	});

	it("partial config merges over the defaults (older sidecars)", () => {
		// Custom mode: flags come from the (partial) config object.
		const r = resolveLinuxWindowButtons({
			mode: "custom",
			show_close: false,
		});
		expect(r.showClose).toBe(false);
		expect(r.showMinimize).toBe(true);
		expect(r.showMaximize).toBe(true);
		expect(r.side).toBe("right");
	});
});
