/**
 * Tests for useLinuxWindowButtons (extracted from App.tsx).
 *
 * Contract: read the user's ``linux_window_buttons`` config + the
 * sidecar's read-only system snapshot via FIELD-level selectors and
 * resolve the effective layout once per change (stable identity for
 * the memoized TitleBar prop).
 */
import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { makeConfig } from "@/__tests__/helpers/fixtures";
import { useLinuxWindowButtons } from "@/hooks/useLinuxWindowButtons";
import { resolveLinuxWindowButtons } from "@/lib/utils/windowButtons";
import { useAppStore } from "@/stores/appStore";

beforeEach(() => {
	useAppStore.setState({ config: makeConfig({}) });
});

describe("useLinuxWindowButtons", () => {
	it("resolves the default layout when no config/system fields exist", () => {
		const { result } = renderHook(() => useLinuxWindowButtons());
		// Absence falls back to the resolver defaults (system mode /
		// right side / all three buttons, system snapshot unavailable).
		expect(result.current).toEqual(resolveLinuxWindowButtons(null, null));
		expect(result.current.side).toBe("right");
		expect(result.current.showMinimize).toBe(true);
		expect(result.current.showMaximize).toBe(true);
		expect(result.current.showClose).toBe(true);
		expect(result.current.followsSystem).toBe(false);
	});

	it("uses the user's custom layout in custom mode", () => {
		useAppStore.setState({
			config: makeConfig({
				linux_window_buttons: {
					mode: "custom",
					side: "left",
					show_minimize: true,
					show_maximize: false,
					show_close: true,
				},
			}),
		});
		const { result } = renderHook(() => useLinuxWindowButtons());
		expect(result.current.side).toBe("left");
		expect(result.current.showMaximize).toBe(false);
		expect(result.current.followsSystem).toBe(false);
	});

	it("follows the sidecar snapshot in system mode", () => {
		useAppStore.setState({
			config: makeConfig({
				linux_window_buttons: {
					mode: "system",
					side: "right",
					show_minimize: true,
					show_maximize: true,
					show_close: true,
				},
				linux_window_buttons_system: {
					desktop_environment: "gnome",
					layout: { side: "left", buttons: ["close"] },
				},
			}),
		});
		const { result } = renderHook(() => useLinuxWindowButtons());
		expect(result.current.side).toBe("left");
		expect(result.current.followsSystem).toBe(true);
	});

	it("keeps a stable result identity across unrelated re-renders", () => {
		const { result, rerender } = renderHook(() => useLinuxWindowButtons());
		const first = result.current;
		rerender();
		rerender();
		expect(result.current).toBe(first);
	});

	it("re-resolves when the config layout changes", () => {
		const { result, rerender } = renderHook(() => useLinuxWindowButtons());
		expect(result.current.side).toBe("right");

		act(() => {
			useAppStore.setState({
				config: makeConfig({
					linux_window_buttons: {
						mode: "custom",
						side: "left",
						show_minimize: true,
						show_maximize: true,
						show_close: true,
					},
				}),
			});
		});
		rerender();
		expect(result.current.side).toBe("left");
	});

	it("does NOT re-resolve on unrelated config writes (field-level selectors)", () => {
		const { result, rerender } = renderHook(() => useLinuxWindowButtons());
		const first = result.current;

		act(() => {
			// mergeConfig always allocates a new top-level config object —
			// only the two window-button fields may re-resolve the layout.
			useAppStore.setState({
				config: makeConfig({ theme_mode: "dark", text_size: 16 }),
			});
		});
		rerender();
		expect(result.current).toBe(first);
	});
});
