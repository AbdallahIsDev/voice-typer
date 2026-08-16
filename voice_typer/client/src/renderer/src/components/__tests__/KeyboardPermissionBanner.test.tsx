/**
 * Tests for the `KeyboardPermissionBanner` component .
 *
 * The banner mirrors `MicrophonePermissionBanner`'s pattern: it renders
 * an amber warning when the OS-level keyboard-monitoring permission
 * (macOS Accessibility / Linux input group + udev rule) is NOT granted.
 *
 * These tests inject the {@link PermissionsResult} directly via the
 * `permissionResult` prop (bypassing the periodic
 * `onboarding_check_permissions` probe in `useKeyboardPermission`) so
 * the assertions are deterministic — no fake-timer dance required to
 * observe the rendered output for a given permission state.
 *
 * Coverage:
 *   1. `state === "denied"` + `needed === true` → banner renders with
 *      the localized title + body text (the "click to fix" prompt).
 *   2. `state === "granted"` → banner is hidden.
 *   3. `needed === false` (Windows / unknown) → banner is hidden.
 *   4. `null` (first probe in flight) → banner is hidden (no
 *      flash-of-banner on mount).
 *   5. macOS user-agent → the "Open settings" deep-link anchor is
 *      rendered with the `x-apple.systempreferences:` href.
 *   6. Linux user-agent → no deep-link anchor (Linux has no equivalent
 *      standard; the banner body text alone tells the user what to do,
 *      mirroring `MicrophonePermissionBanner`).
 */
import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
// Shared stable-mocks preamble (see helpers/stableMocks.tsx):
// `usePython` is mocked so the `useKeyboardPermission()` hook's
// `call("onboarding_check_permissions")` is a no-op that never resolves
// — the hook stays in its initial `null` state for the duration of each
// test, and the `permissionResult` prop injection is what drives the
// rendered output. This keeps the tests deterministic without fake
// timers.
import {
	pythonMock,
	resetStableMocks,
	stableMocks,
} from "@/__tests__/helpers/stableMocks";
import type { PermissionsResult } from "@/types/ipc";

const { mockCall } = stableMocks;

vi.mock("@/hooks/usePython", () => pythonMock());

import { KeyboardPermissionBanner } from "@/components/KeyboardPermissionBanner";

const DENIED: PermissionsResult = {
	platform: "macos",
	state: "denied",
	needed: true,
	instructions: null,
};

const GRANTED: PermissionsResult = {
	platform: "macos",
	state: "granted",
	needed: false,
	instructions: null,
};

const NOT_NEEDED: PermissionsResult = {
	platform: "windows",
	state: "unknown",
	needed: false,
	instructions: null,
};

describe("KeyboardPermissionBanner ", () => {
	let originalUserAgent: string;

	beforeEach(() => {
		originalUserAgent = navigator.userAgent;
		resetStableMocks();
		// Never-resolving promise: the probe stays in flight so
		// `probed === null` and the `permissionResult` prop is
		// what the component renders against.
		mockCall.mockReturnValue(new Promise(() => {}));
	});

	afterEach(() => {
		// Restore the real userAgent so platform-specific
		// branching in later test files isn't affected.
		Object.defineProperty(navigator, "userAgent", {
			value: originalUserAgent,
			configurable: true,
		});
	});

	function setUserAgent(ua: string) {
		Object.defineProperty(navigator, "userAgent", {
			value: ua,
			configurable: true,
		});
	}

	it("renders the amber banner when state === 'denied' && needed === true", () => {
		setUserAgent(
			"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) AppleWebKit/605.1.15",
		);
		render(<KeyboardPermissionBanner permissionResult={DENIED} />);

		// Title + body both surface so screen-reader users get the
		// full "click to fix" prompt.
		expect(
			screen.getByText("Hotkeys require accessibility permission"),
		).toBeInTheDocument();
		expect(
			screen.getByText(
				"Hotkeys require accessibility permission — click to fix",
			),
		).toBeInTheDocument();

		// role="alert" so assistive tech announces the banner.
		expect(screen.getByRole("alert")).toBeInTheDocument();

		// macOS deep-link anchor is rendered.
		const link = screen.getByRole("link", {
			name: "Open the operating system accessibility settings",
		});
		expect(link).toHaveAttribute(
			"href",
			"x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
		);
	});

	it("is hidden when state === 'granted'", () => {
		setUserAgent("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15)");
		render(<KeyboardPermissionBanner permissionResult={GRANTED} />);
		expect(screen.queryByRole("alert")).toBeNull();
	});

	it("is hidden when needed === false (Windows / unknown)", () => {
		setUserAgent("Mozilla/5.0 (Windows NT 10.0; Win64; x64)");
		render(<KeyboardPermissionBanner permissionResult={NOT_NEEDED} />);
		expect(screen.queryByRole("alert")).toBeNull();
	});

	it("is hidden while the first probe is in flight (permissionResult === null)", () => {
		setUserAgent("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15)");
		render(<KeyboardPermissionBanner permissionResult={null} />);
		expect(screen.queryByRole("alert")).toBeNull();
	});

	it("renders the banner body but NO deep-link anchor on Linux (no equivalent standard)", () => {
		setUserAgent(
			"Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
		);
		render(<KeyboardPermissionBanner permissionResult={DENIED} />);

		// Banner body is still shown so the user knows what to do.
		expect(
			screen.getByText(
				"Hotkeys require accessibility permission — click to fix",
			),
		).toBeInTheDocument();

		// No anchor — Linux has no standard OS-privacy deep-link,
		// and the renderer cannot directly invoke pkexec. Mirrors
		// `MicrophonePermissionBanner`'s Linux branch.
		expect(screen.queryByRole("link")).toBeNull();
	});
});
