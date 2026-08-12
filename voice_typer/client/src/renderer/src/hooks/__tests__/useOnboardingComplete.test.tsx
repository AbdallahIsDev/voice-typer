/**
 * Tests for useOnboardingComplete (extracted from App.tsx, EO-28).
 *
 * Contract: on completion, navigate to home, then re-apply the theme
 * from the freshly-fetched config. Non-fatal on get_config failure.
 */
import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useOnboardingComplete } from "@/hooks/useOnboardingComplete";

describe("useOnboardingComplete", () => {
	it("navigates home and reloads the theme when config has theme_mode", async () => {
		const navigate = vi.fn();
		const reloadThemeFromConfig = vi.fn().mockResolvedValue(undefined);
		const call = vi.fn().mockResolvedValue({ theme_mode: "dark" });

		const { result } = renderHook(() =>
			useOnboardingComplete({ navigate, call, reloadThemeFromConfig }),
		);

		await act(async () => {
			await result.current();
		});

		expect(navigate).toHaveBeenCalledWith("home");
		expect(call).toHaveBeenCalledWith("get_config");
		expect(reloadThemeFromConfig).toHaveBeenCalledTimes(1);
	});

	it("does NOT reload the theme when config has no theme_mode", async () => {
		const navigate = vi.fn();
		const reloadThemeFromConfig = vi.fn();
		const call = vi.fn().mockResolvedValue({ hotkey: "<ctrl>+<alt>+v" });

		const { result } = renderHook(() =>
			useOnboardingComplete({ navigate, call, reloadThemeFromConfig }),
		);

		await act(async () => {
			await result.current();
		});

		expect(navigate).toHaveBeenCalledWith("home");
		expect(reloadThemeFromConfig).not.toHaveBeenCalled();
	});

	it("navigates home even when get_config fails (non-fatal)", async () => {
		const navigate = vi.fn();
		const reloadThemeFromConfig = vi.fn();
		const call = vi.fn().mockRejectedValue(new Error("backend down"));
		const warn = vi.spyOn(console, "warn").mockImplementation(() => {});

		const { result } = renderHook(() =>
			useOnboardingComplete({ navigate, call, reloadThemeFromConfig }),
		);

		await act(async () => {
			await result.current();
		});

		expect(navigate).toHaveBeenCalledWith("home");
		expect(reloadThemeFromConfig).not.toHaveBeenCalled();
		expect(warn).toHaveBeenCalled();
		warn.mockRestore();
	});

	it("reload failure is non-fatal (no throw)", async () => {
		const navigate = vi.fn();
		const reloadThemeFromConfig = vi
			.fn()
			.mockRejectedValue(new Error("theme fail"));
		const call = vi.fn().mockResolvedValue({ theme_mode: "dark" });
		const warn = vi.spyOn(console, "warn").mockImplementation(() => {});

		const { result } = renderHook(() =>
			useOnboardingComplete({ navigate, call, reloadThemeFromConfig }),
		);

		await act(async () => {
			await expect(result.current()).resolves.toBeUndefined();
		});

		expect(navigate).toHaveBeenCalledWith("home");
		warn.mockRestore();
	});
});
