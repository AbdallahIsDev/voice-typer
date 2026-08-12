/**
 *  (client_pages) — regression tests for the fixes applied in this
 * sub-agent session. Each describe block pins one finding so a future
 * regression points at the exact contract that broke.
 *
 *   -   Onboarding mic auto-select prefers `default: true`
 *               device (not just `microphones[0]`). MicrophoneStep
 *               renders a "Default" badge on the default-flagged device
 *               and a "BT" badge on Bluetooth/HFP devices. Continue is
 *               disabled when no mics are detected on the Microphone
 *               step.
 *   -  History "Clear All" button has a permanent destructive
 *               visual cue (text-destructive + border-destructive at
 *               rest, not just on hover).
 *   -      Home.tsx extraction: the page imports the extracted
 *               subcomponents from `./home/` AND keeps the
 *               `debouncedRefreshFromEvent` declaration in the
 *               composition root (R7-F13 contract preserved).
 */
import {
	cleanup,
	fireEvent,
	render,
	screen,
	waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// ── Hoisted mocks (shared across describe blocks) ─────────────────────

const { mockCall, mockShowSnack } = vi.hoisted(() => ({
	mockCall: vi.fn(),
	mockShowSnack: vi.fn(),
}));

vi.mock("@/hooks/usePython", () => ({
	usePython: () => ({ call: mockCall }),
	usePythonEvent: () => {},
}));

vi.mock("@/hooks/useSnackbar", () => ({
	useSnackbar: () => ({ showSnack: mockShowSnack }),
	showUndoableToast: vi.fn(),
}));

vi.mock("@/hooks/useLastUpdated", () => ({
	useLastUpdated: () => ({
		agoLabel: "",
		markUpdated: vi.fn(),
		refreshing: false,
		withRefresh: async <T,>(op: () => Promise<T>): Promise<T> => op(),
	}),
}));

vi.mock("@/hooks/useNavigation", () => ({
	useNavigation: () => ({ navigate: vi.fn() }),
}));

vi.mock("@/hugeicons/react", () => ({
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

vi.mock("@hugeicons/core-free-icons", async () => {
	const { createHugeiconsMock } = await import(
		"@/__tests__/helpers/hugeicons-mock"
	);
	return createHugeiconsMock();
});

vi.mock("sonner", () => ({
	toast: {
		success: vi.fn(),
		error: vi.fn(),
		warning: vi.fn(),
		info: vi.fn(),
		dismiss: vi.fn(),
	},
	Toaster: () => null,
}));

vi.mock("next-themes", () => ({
	useTheme: () => ({ theme: "light" as const }),
}));

// Inline the Select mock so SelectItem children (the Default + BT
// badges) render in the DOM without Radix's pointer-capture machinery.
vi.mock("@/components/ui/select", () => ({
	Select: ({ children }: { children: React.ReactNode }) => (
		<div data-testid="select-root">{children}</div>
	),
	SelectTrigger: ({
		children,
		...props
	}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
		children?: React.ReactNode;
	}) => (
		<button type="button" {...props}>
			{children}
		</button>
	),
	SelectValue: ({ placeholder }: { placeholder?: string }) => (
		<span>{placeholder ?? ""}</span>
	),
	SelectContent: ({ children }: { children: React.ReactNode }) => (
		<div data-testid="select-content">{children}</div>
	),
	SelectItem: ({
		children,
		value,
	}: {
		children?: React.ReactNode;
		value: string;
		textValue?: string;
	}) => (
		<div data-value={value} role="option" tabIndex={-1}>
			{children}
		</div>
	),
}));

import OnboardingPage from "@/pages/Onboarding";

const STEP_NAMES = [
	"Welcome",
	"Microphone",
	"Permissions",
	"Hotkey",
	"Model",
	"Done",
] as const;

// jsdom doesn't implement the Pointer Capture API; stub it so Radix
// Select (in the Onboarding Microphone step) doesn't throw.
if (
	typeof Element !== "undefined" &&
	typeof Element.prototype.hasPointerCapture !== "function"
) {
	Element.prototype.hasPointerCapture = function hasPointerCapture() {
		return false;
	};
	Element.prototype.setPointerCapture = function setPointerCapture() {};
	Element.prototype.releasePointerCapture = function releasePointerCapture() {};
}

beforeEach(() => {
	mockCall.mockReset();
	mockShowSnack.mockReset();
	localStorage.clear();
});

afterEach(() => {
	cleanup();
});

//mic auto-select prefers `default: true` ─────────────────

describe("S2-CR-39: Onboarding mic auto-select prefers default-flagged device", () => {
	it("useOnboardingWizard source prefers default-flagged mic over microphones[0]", async () => {
		const fs = await import("node:fs");
		const src = fs.readFileSync(
			"src/renderer/src/pages/onboarding/hooks/useOnboardingWizard.ts",
			"utf8",
		);
		// The fix: instead of `mics.microphones[0].id`, the wizard now
		// searches for a mic with `default === true` and falls back to
		// `[0]` only when no default is flagged.
		expect(src).toContain("m.default === true");
		expect(src).toContain("mics.microphones.find(");
		// Sanity: the prior buggy fallback (`microphones[0].id` as the
		// unconditional pick) is gone — the `[0]` reference is now only
		// used as the nullish-coalesced fallback (via the `fallback`
		// local, `(defaultMic ?? fallback)?.id ?? prev` — the
		// `?? ` chain, never a bare `[0]` pick).
		expect(src).toContain("const fallback = mics.microphones[0]");
		expect(src).toContain("defaultMic ?? fallback");
	});

	it("MicrophoneOption type declares optional default + is_bluetooth fields", async () => {
		const fs = await import("node:fs");
		const src = fs.readFileSync(
			"src/renderer/src/pages/onboarding/lib/types.ts",
			"utf8",
		);
		expect(src).toMatch(/default\?:\s*boolean/);
		expect(src).toMatch(/is_bluetooth\?:\s*boolean/);
	});

	it("auto-selects the default-flagged device when multiple mics are present", async () => {
		// Backend returns 3 mics; only the second is flagged `default: true`.
		// The wizard must auto-select mic-2 (the default), NOT mic-1 (which
		// is first in enumeration order — the prior buggy behaviour).
		let capturedMicId: string | null = null;
		mockCall.mockImplementation(
			(type: string, payload?: Record<string, unknown>) => {
				switch (type) {
					case "onboarding_start":
						return Promise.resolve({
							step: 1,
							total_steps: 6,
							step_name: STEP_NAMES[1],
						});
					case "onboarding_set_microphone":
						capturedMicId = (payload?.mic_id as string | null) ?? null;
						return Promise.resolve({});
					case "onboarding_next_step":
						return Promise.resolve({
							step: 2,
							total_steps: 6,
							step_name: STEP_NAMES[2],
						});
					case "get_config":
						return Promise.resolve({
							hotkey: "<caps_lock>",
							model_size: "small.en",
							microphone: "",
						});
					case "onboarding_get_microphones":
						return Promise.resolve({
							microphones: [
								{ id: "mic-1", name: "USB Mic", default: false },
								{ id: "mic-2", name: "Built-in", default: true },
								{
									id: "mic-3",
									name: "Bluetooth",
									default: false,
									is_bluetooth: true,
								},
							],
						});
					case "onboarding_get_hotkey_presets":
						return Promise.resolve({ presets: ["<f2>", "<caps_lock>"] });
					case "onboarding_get_model_options":
						return Promise.resolve({
							models: [
								{
									name: "small.en",
									size: "~466MB",
									speed: "Fast",
									description: "Small",
								},
							],
						});
					case "onboarding_check_permissions":
						return Promise.resolve({
							platform: "linux",
							state: "granted",
							needed: false,
							instructions: null,
						});
					default:
						return Promise.resolve({});
				}
			},
		);

		render(<OnboardingPage onComplete={() => {}} />);

		// Wait for the Microphone step to render its Select.
		await waitFor(() => {
			expect(screen.getByTestId("select-content")).toBeTruthy();
		});

		// Click Continue — the `onboarding_set_microphone` call should
		// carry the DEFAULT mic's id, not the first one.
		const continueBtn = await screen.findByRole("button", {
			name: "Continue",
		});
		fireEvent.click(continueBtn);

		await waitFor(() => {
			expect(capturedMicId).toBe("mic-2");
		});
	});

	it("MicrophoneStep renders Default badge on the default-flagged device + BT badge on bluetooth device", async () => {
		mockCall.mockImplementation((type: string) => {
			switch (type) {
				case "onboarding_start":
					return Promise.resolve({
						step: 1,
						total_steps: 6,
						step_name: STEP_NAMES[1],
					});
				case "get_config":
					return Promise.resolve({
						hotkey: "<caps_lock>",
						model_size: "small.en",
						microphone: "",
					});
				case "onboarding_get_microphones":
					return Promise.resolve({
						microphones: [
							{ id: "mic-1", name: "USB Mic", default: false },
							{ id: "mic-2", name: "Built-in", default: true },
							{
								id: "mic-3",
								name: "BT Headset",
								default: false,
								is_bluetooth: true,
							},
						],
					});
				case "onboarding_get_hotkey_presets":
					return Promise.resolve({ presets: ["<f2>"] });
				case "onboarding_get_model_options":
					return Promise.resolve({
						models: [
							{
								name: "small.en",
								size: "~466MB",
								speed: "Fast",
								description: "Small",
							},
						],
					});
				case "onboarding_check_permissions":
					return Promise.resolve({
						platform: "linux",
						state: "granted",
						needed: false,
						instructions: null,
					});
				default:
					return Promise.resolve({});
			}
		});

		render(<OnboardingPage onComplete={() => {}} />);

		// Wait for the Select content to render the per-mic items.
		await waitFor(() => {
			expect(screen.getByTestId("select-content")).toBeTruthy();
		});

		// The Default badge exists for mic-2 only.
		expect(
			document.querySelector('[data-testid="mic-default-badge-mic-2"]'),
		).not.toBeNull();
		expect(
			document.querySelector('[data-testid="mic-default-badge-mic-1"]'),
		).toBeNull();
		// The BT badge exists for mic-3 only.
		expect(
			document.querySelector('[data-testid="mic-bluetooth-badge-mic-3"]'),
		).not.toBeNull();
		expect(
			document.querySelector('[data-testid="mic-bluetooth-badge-mic-1"]'),
		).toBeNull();
	});

	it("Onboarding.tsx source disables Continue when no mics detected on Microphone step", async () => {
		const fs = await import("node:fs");
		const src = fs.readFileSync(
			"src/renderer/src/pages/Onboarding.tsx",
			"utf8",
		);
		//(d): the Continue button's disabled prop must include
		// a guard for the no-mics case on the Microphone step. We assert
		// on the literal `isMicStepBlocked` identifier so a regression
		// (e.g. removing the guard from the disabled prop) fails loudly.
		expect(src).toContain("isMicStepBlocked");
		// The guard itself must check both the step name and the mics
		// array length.
		expect(src).toMatch(
			/step\.step_name === ["']Microphone["']\s*&&\s*microphones\.length === 0/,
		);
		// And the disabled prop must reference the guard.
		expect(src).toMatch(/isMicStepBlocked/);
		// Strip comments and verify the guard appears inside the
		// disabled={...} prop (not just as a standalone const).
		const stripped = src
			.replace(/\/\*[\s\S]*?\*\//g, "")
			.replace(/\/\/.*$/gm, "");
		expect(stripped).toMatch(/disabled=\{[\s\S]*isMicStepBlocked[\s\S]*\}/);
	});

	it("MicrophoneStep.tsx source renders a Refresh button when no mics + onRefreshMics provided", async () => {
		const fs = await import("node:fs");
		const src = fs.readFileSync(
			"src/renderer/src/pages/onboarding/components/MicrophoneStep.tsx",
			"utf8",
		);
		// Recovery affordance: the no-mics branch must call onRefreshMics.
		expect(src).toContain("onRefreshMics");
		expect(src).toMatch(/onClick=\{onRefreshMics\}/);
	});
});

// ── S5-CR-104: History Clear All permanent destructive cue ────────────

describe("S5-CR-104: History Clear All button has permanent destructive visual cue", () => {
	it("History.tsx source uses text-destructive at rest (not just on hover)", async () => {
		const fs = await import("node:fs");
		const src = fs.readFileSync("src/renderer/src/pages/History.tsx", "utf8");
		// The fix: the Clear All button's className must include
		// `text-destructive` at rest (not `text-(--text-muted)` with
		// only `hover:text-red-400`). We assert on the literal
		// substring so a regression to the muted-at-rest pattern fails
		// loudly.
		expect(src).toContain("text-destructive/80");
		expect(src).toContain("border-destructive/40");
		// The prior buggy pattern (muted at rest, red only on hover)
		// must be gone.
		expect(src).not.toContain("text-(--text-muted) hover:text-red-400");
	});

	it("Clear All button className is distinct from the Favorites toggle className", async () => {
		const fs = await import("node:fs");
		const src = fs.readFileSync("src/renderer/src/pages/History.tsx", "utf8");
		// Strip comments so legacy doc strings mentioning the old class
		// don't false-positive.
		const stripped = src
			.replace(/\/\*[\s\S]*?\*\//g, "")
			.replace(/\/\/.*$/gm, "");
		// The Clear All button must reference the destructive token.
		expect(stripped).toMatch(/text-destructive\/80/);
		// The button's JSX block (the one with `onClick={handleClearAll}`)
		// must contain the destructive class. Search for the onClick
		// binding and assert the className appears within a reasonable
		// window after it.
		const onClickIdx = stripped.indexOf("onClick={handleClearAll}");
		expect(onClickIdx).toBeGreaterThan(-1);
		const slice = stripped.slice(onClickIdx, onClickIdx + 1200);
		expect(slice).toContain("text-destructive");
		expect(slice).toContain("border-destructive");
	});
});

//Home.tsx extraction preserves the R7-F13 contract ──────────

describe("EC-12: Home.tsx extraction (subcomponents moved to ./home/)", () => {
	it("Home.tsx imports the extracted subcomponents from ./home/", async () => {
		const fs = await import("node:fs");
		const src = fs.readFileSync("src/renderer/src/pages/Home.tsx", "utf8");
		// Each extracted piece must be imported (not inlined).
		expect(src).toContain("./home/lib/cache");
		expect(src).toContain("./home/lib/constants");
		expect(src).toContain("./home/lib/status");
		expect(src).toContain("./home/components/RecordingStatusPill");
		expect(src).toContain("./home/components/MicToggleButton");
		expect(src).toContain("./home/components/LastTranscriptionPreview");
		expect(src).toContain("./home/components/RecordingErrorCard");
		expect(src).toContain("./home/hooks/useFirstRecordingCelebration");
	});

	it("Home.tsx source declares debouncedRefreshFromEvent via useCallback (R7-F13 preserved)", async () => {
		const fs = await import("node:fs");
		const src = fs.readFileSync("src/renderer/src/pages/Home.tsx", "utf8");
		// R7-F13 contract: the shared refresh callback is declared via
		// useCallback in Home.tsx (NOT extracted to a hook) so the test
		// can grep for the declaration.
		expect(src).toContain("const debouncedRefreshFromEvent = useCallback(");
		// And passed to both usePythonEvent subscriptions.
		const uses = src.match(/debouncedRefreshFromEvent\b/g) ?? [];
		// 1 declaration + at least 2 subscription uses.
		expect(uses.length).toBeGreaterThanOrEqual(3);
	});

	it("Home.tsx no longer inlines RecordingStatusPill / MicToggleButton / LastTranscriptionPreview / RecordingErrorCard", async () => {
		const fs = await import("node:fs");
		const src = fs.readFileSync("src/renderer/src/pages/Home.tsx", "utf8");
		// Strip comments before checking — the extraction leaves a
		// header comment naming the extracted files.
		const stripped = src
			.replace(/\/\*[\s\S]*?\*\//g, "")
			.replace(/\/\/.*$/gm, "");
		// The inline function declarations must be gone.
		expect(stripped).not.toMatch(/function RecordingStatusPill\b/);
		expect(stripped).not.toMatch(/function MicToggleButton\b/);
		expect(stripped).not.toMatch(/function LastTranscriptionPreview\b/);
		expect(stripped).not.toMatch(/function RecordingErrorCard\b/);
		// The module-level cache helpers must be gone (they live in
		// ./home/lib/cache now).
		expect(stripped).not.toMatch(/function loadCachedRecent\b/);
		expect(stripped).not.toMatch(/function persistRecent\b/);
		// The module-level STATUS_COLORS const must be gone (it lives
		// in ./home/lib/constants now).
		expect(stripped).not.toMatch(/const STATUS_COLORS\s*:/);
	});

	it("extracted constants module exports the expected keys", async () => {
		const fs = await import("node:fs");
		const src = fs.readFileSync(
			"src/renderer/src/pages/home/lib/constants.ts",
			"utf8",
		);
		expect(src).toContain("RECENT_CACHE_KEY");
		expect(src).toContain("STATS_CACHE_KEY");
		expect(src).toContain("FIRST_RECORD_CELEBRATED_KEY");
		expect(src).toContain("FORCE_CANCEL_DELAY_MS");
		expect(src).toContain("LAST_TEXT_AUTO_CLEAR_MS");
		expect(src).toContain("STATUS_COLORS");
	});

	it("extracted cache module declares the four pure helpers", async () => {
		const fs = await import("node:fs");
		const src = fs.readFileSync(
			"src/renderer/src/pages/home/lib/cache.ts",
			"utf8",
		);
		expect(src).toContain("export function loadCachedRecent");
		expect(src).toContain("export function loadCachedStats");
		expect(src).toContain("export function persistRecent");
		expect(src).toContain("export function persistStats");
	});

	it("extracted status module declares normalizeHotkey / statusLabelFor / statusKeyFor", async () => {
		const fs = await import("node:fs");
		const src = fs.readFileSync(
			"src/renderer/src/pages/home/lib/status.ts",
			"utf8",
		);
		expect(src).toContain("export function normalizeHotkey");
		expect(src).toContain("export function statusLabelFor");
		expect(src).toContain("export function statusKeyFor");
	});
});
