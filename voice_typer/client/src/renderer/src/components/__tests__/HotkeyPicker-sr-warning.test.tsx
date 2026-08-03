/**
 * Tests for the HotkeyPicker screen-reader-conflict warning banner.
 *
 * When the currently-assigned hotkey conflicts with a default
 * screen-reader modifier key on the user's platform (e.g.
 * ``<caps_lock>`` on macOS → VoiceOver, or on Windows →
 * Narrator/NVDA/JAWS), the picker renders a localized amber warning
 * banner below the capture button. This file verifies:
 *
 *   - On macOS, assigning ``<caps_lock>`` renders the banner with
 *     the expected fallback text (the i18n key
 *     ``hotkeyPicker.capsLockSrConflictWarning`` is owned by a
 *     separate task — until it lands, ``HotkeyPicker._resolveSrConflictWarning``
 *     falls back to a hardcoded English string).
 *   - On Linux, the SAME value does NOT render the banner (Orca uses
 *     Insert by default; Caps Lock is not reserved).
 *   - A non-conflicting value (``<f2>``) does NOT render the banner
 *     on any platform.
 *   - The banner uses ``role="status"`` + ``aria-live="polite"`` (NOT
 *     ``role="alert"``) because the warning is advisory — the user
 *     can still keep the conflicting hotkey.
 *   - The banner contains the ``data-testid="sr-conflict-warning"``
 *     selector so consumers (e.g. RecordingSettingsSection /
 *     HotkeyStep) can assert against it without relying on text
 *     content (which changes when the i18n key lands).
 *
 * Platform detection stubs ``navigator.platform`` per-test using
 * ``vi.stubGlobal`` — same pattern as hotkey-utils.test.ts and
 * checkHotkeyConflict-sr.test.ts.
 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { HotkeyPicker } from "../hotkey/HotkeyPicker";

/**
 * Stub ``navigator.platform`` to a known value. See
 * checkHotkeyConflict-sr.test.ts for why ``vi.stubGlobal`` is the
 * right tool (jsdom defines ``navigator`` as a non-configurable
 * getter on some versions).
 */
function stubPlatform(platform: string): void {
	const realUserAgent =
		typeof navigator !== "undefined" ? navigator.userAgent : "";
	vi.stubGlobal("navigator", {
		platform,
		userAgent: realUserAgent,
	});
}

function renderPicker(
	overrides: { value?: string; mode?: "single" | "combo" } = {},
) {
	return render(
		<HotkeyPicker
			value={overrides.value ?? ""}
			onChange={vi.fn()}
			mode={overrides.mode ?? "single"}
			aria-label="Dictation key"
		/>,
	);
}

describe("HotkeyPicker — screen-reader conflict warning banner", () => {
	beforeEach(() => {
		// Reset navigator between tests so a forgotten stub doesn't
		// leak across the suite.
		vi.unstubAllGlobals();
	});
	afterEach(() => {
		cleanup();
		vi.unstubAllGlobals();
	});

	// ── macOS: banner shows for <caps_lock> ─────────────────────────────
	describe("macOS (navigator.platform = 'MacIntel')", () => {
		beforeEach(() => stubPlatform("MacIntel"));

		it("renders the SR-conflict warning banner when value is <caps_lock>", () => {
			renderPicker({ value: "<caps_lock>" });
			const banner = screen.getByTestId("sr-conflict-warning");
			expect(banner).toBeInTheDocument();
			// role="status" (polite live region) — NOT role="alert"
			// (assertive), because the warning is advisory, not a
			// blocking error.
			// <output> has the implicit ARIA role of status — assert the
			// COMPUTED role (toHaveRole) rather than the literal role
			// attribute (which <output> doesn't carry).
			expect(banner).toHaveRole("status");
			expect(banner).toHaveAttribute("aria-live", "polite");
		});

		it("banner text is the hardcoded English fallback (i18n key not yet registered)", () => {
			// The i18n key hotkeyPicker.capsLockSrConflictWarning is
			//owned by a separate task (). Until that lands,
			// t() returns the raw key string and HotkeyPicker falls
			// back to a hardcoded English string. This test pins the
			// fallback text so a regression is caught immediately.
			renderPicker({ value: "<caps_lock>" });
			const banner = screen.getByTestId("sr-conflict-warning");
			expect(banner.textContent ?? "").toMatch(/Caps Lock/i);
			expect(banner.textContent ?? "").toMatch(/VoiceOver|Narrator/i);
			expect(banner.textContent ?? "").toMatch(/screen reader/i);
		});

		it("does NOT render the banner for a non-conflicting value (<f2>)", () => {
			renderPicker({ value: "<f2>" });
			expect(screen.queryByTestId("sr-conflict-warning")).toBeNull();
			// Also assert no role="status" live region is rendered
			// (the SR banner is the only role="status" element the
			// picker produces in the idle state).
			expect(screen.queryByRole("status")).toBeNull();
		});

		it("does NOT render the banner when value is empty", () => {
			renderPicker({ value: "" });
			expect(screen.queryByTestId("sr-conflict-warning")).toBeNull();
		});

		it("does NOT render role='alert' (the warning is advisory, not a blocking error)", () => {
			// Sanity check: when only the SR warning is present (no
			// validation error), there's no role="alert" live region.
			// This proves the SR warning doesn't accidentally use the
			// assertive channel.
			renderPicker({ value: "<caps_lock>" });
			expect(screen.queryByRole("alert")).toBeNull();
			expect(screen.getByRole("status")).toBeInTheDocument();
		});
	});

	// ── Windows: banner shows for <caps_lock> ───────────────────────────
	describe("Windows (navigator.platform = 'Win32')", () => {
		beforeEach(() => stubPlatform("Win32"));

		it("renders the SR-conflict warning banner when value is <caps_lock>", () => {
			renderPicker({ value: "<caps_lock>" });
			const banner = screen.getByTestId("sr-conflict-warning");
			expect(banner).toBeInTheDocument();
			// <output> has the implicit ARIA role of status — assert the
			// COMPUTED role (toHaveRole) rather than the literal role
			// attribute (which <output> doesn't carry).
			expect(banner).toHaveRole("status");
		});

		it("banner text mentions Narrator (one of the Windows SR products)", () => {
			renderPicker({ value: "<caps_lock>" });
			const banner = screen.getByTestId("sr-conflict-warning");
			expect(banner.textContent ?? "").toMatch(/Narrator/i);
		});
	});

	// ── Linux: NO banner for <caps_lock> (Orca uses Insert) ─────────────
	describe("Linux (navigator.platform = 'Linux x86_64')", () => {
		beforeEach(() => stubPlatform("Linux x86_64"));

		it("does NOT render the SR-conflict warning banner for <caps_lock>", () => {
			// Linux Orca uses Insert by default; Caps Lock is safe to
			// assign. The empty ``linux`` array in
			// hotkey_reserved.json::screen_reader_conflicts pins this.
			renderPicker({ value: "<caps_lock>" });
			expect(screen.queryByTestId("sr-conflict-warning")).toBeNull();
			expect(screen.queryByRole("status")).toBeNull();
		});

		it("does NOT render the banner for <f2>", () => {
			renderPicker({ value: "<f2>" });
			expect(screen.queryByTestId("sr-conflict-warning")).toBeNull();
		});
	});

	// ── Banner visibility toggles with value changes ────────────────────
	describe("banner visibility tracks value changes", () => {
		beforeEach(() => stubPlatform("MacIntel"));

		it("banner appears when value changes from <f2> to <caps_lock>", () => {
			const { rerender } = renderPicker({ value: "<f2>" });
			expect(screen.queryByTestId("sr-conflict-warning")).toBeNull();

			rerender(
				<HotkeyPicker
					value="<caps_lock>"
					onChange={vi.fn()}
					mode="single"
					aria-label="Dictation key"
				/>,
			);
			expect(screen.getByTestId("sr-conflict-warning")).toBeInTheDocument();
		});

		it("banner disappears when value changes from <caps_lock> to <f2>", () => {
			const { rerender } = renderPicker({ value: "<caps_lock>" });
			expect(screen.getByTestId("sr-conflict-warning")).toBeInTheDocument();

			rerender(
				<HotkeyPicker
					value="<f2>"
					onChange={vi.fn()}
					mode="single"
					aria-label="Dictation key"
				/>,
			);
			expect(screen.queryByTestId("sr-conflict-warning")).toBeNull();
		});
	});
});
