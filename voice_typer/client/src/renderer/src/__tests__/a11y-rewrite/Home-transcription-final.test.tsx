/**
 *  vitest rewrite — behavioral test for `Home.tsx` listener count.
 *
 * Replaces the following string-pattern Python test from
 * `tests/test_feature_hardening_regressions.py`:
 *   - TestHomeRegistersSingleTranscriptionFinalListener::test_only_one_transcription_final_listener
 *
 * The Python test counted occurrences of the literal
 * `usePythonEvent("transcription_final"` (and its single-quote
 * variant) inside Home.tsx source.  This is brittle: it fails on
 * innocent refactors (extracting the handler to a hook, switching to
 * a `useMemo`+`useEffect` pattern, using a constant for the event
 * name) and it passes even when the listener is registered twice
 * via a different syntax.  The vitest version below mocks
 * `usePythonEvent`, mounts the real Home page, and asserts the mock
 * was called with the `"transcription_final"` event name exactly
 * once.
 *
 * The corresponding Python test is skipped via `@pytest.mark.skip`
 * with a pointer back to this file.  It is NOT deleted.
 */
import { cleanup, render } from "@testing-library/react";
import { toast } from "sonner";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockCall, mockPythonEvent } = vi.hoisted(() => ({
	mockCall: vi.fn(),
	mockPythonEvent: vi.fn(),
}));

vi.mock("@/hooks/usePython", () => ({
	usePython: () => ({ call: mockCall }),
	usePythonEvent: mockPythonEvent,
}));

vi.mock("@hugeicons/react", () => ({
	HugeiconsIcon: ({ icon }: { icon?: { name?: string } }) => (
		<span data-testid="hugeicon" data-name={icon?.name} />
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

describe("Home transcription_final listener — RW-0 rewrite of test_only_one_transcription_final_listener", () => {
	beforeEach(() => {
		mockCall.mockReset();
		mockPythonEvent.mockReset();
		mockCall.mockImplementation(() => new Promise(() => {}));
		localStorage.clear();
		vi.resetModules();
		vi.mocked(toast.error).mockClear();
		vi.mocked(toast.success).mockClear();
	});

	afterEach(() => {
		cleanup();
	});

	it("registers exactly one usePythonEvent listener for transcription_final", async () => {
		const { default: Home } = await import("@/pages/Home");
		render(<Home />);

		// The Python invariant: Home.tsx source contains
		// exactly one occurrence of
		// `usePythonEvent("transcription_final"`.
		// Behavioral: usePythonEvent was called with the
		// event name "transcription_final" exactly once.
		const tfCalls = mockPythonEvent.mock.calls.filter(
			(args) => args[0] === "transcription_final",
		);
		expect(tfCalls.length).toBe(1);
	});

	it("registers the listener on every mount (no duplicate-after-remount regression)", async () => {
		// Mount, unmount, re-mount — each mount must
		// register the listener exactly once.  This catches
		// a regression where StrictMode or a manual remount
		// accidentally double-subscribes.
		const { default: Home } = await import("@/pages/Home");
		const { unmount: unmount1 } = render(<Home />);
		const tfAfterMount1 = mockPythonEvent.mock.calls.filter(
			(args) => args[0] === "transcription_final",
		).length;
		expect(tfAfterMount1).toBe(1);

		unmount1();
		const { unmount: unmount2 } = render(<Home />);
		const tfAfterMount2 = mockPythonEvent.mock.calls.filter(
			(args) => args[0] === "transcription_final",
		).length;
		expect(tfAfterMount2).toBe(2);
		unmount2();
	});

	it("does not register the listener with a typo'd event name", async () => {
		const { default: Home } = await import("@/pages/Home");
		render(<Home />);

		// Defensive: catch a regression where the event name
		// is silently misspelled (e.g. "transcription_finale"
		// or "transcriptionFinal").  None of these should
		// ever be passed to usePythonEvent.
		const typoCandidates = [
			"transcription_finale",
			"transcriptionFinal",
			"transcription-final",
			"transcription_finalize",
			"transcription_finished",
			"transcription_complete",
		];
		for (const typo of typoCandidates) {
			const typoCalls = mockPythonEvent.mock.calls.filter(
				(args) => args[0] === typo,
			);
			expect(typoCalls.length).toBe(0);
		}
	});
});

//regression guard: ``handleToggle`` in Home.tsx previously
// swallowed IPC failures from ``toggle_dictation`` with only a
// ``console.error`` — leaving the user staring at a spinner that
// disappeared with no explanation. The fix surfaces a localized
// ``toast.error(t("home.toggleFailed"))`` in the catch block (mirroring
// the sibling handlers). This describe block mounts Home, simulates an
// IPC rejection on the toggle channel, and asserts the error toast
// fires — so a future refactor that drops the toast (or changes the
// catch to ``catch {}``) fails this test loudly instead of regressing
// silently.
describe("Home handleToggle error toast — XA-12-3 regression guard", () => {
	beforeEach(() => {
		mockCall.mockReset();
		mockPythonEvent.mockReset();
		mockCall.mockImplementation(() => new Promise(() => {}));
		localStorage.clear();
		vi.resetModules();
		vi.mocked(toast.error).mockClear();
		vi.mocked(toast.success).mockClear();
	});

	afterEach(() => {
		cleanup();
	});

	it("fires toast.error when toggle_dictation rejects (no silent swallow)", async () => {
		const { default: Home } = await import("@/pages/Home");
		const { findByRole } = render(<Home />);

		// Override the default never-resolving implementation:
		// when the renderer calls ``call("toggle_dictation")``
		// we reject, simulating a backend / IPC failure.
		mockCall.mockImplementation((cmd: string) => {
			if (cmd === "toggle_dictation") {
				return Promise.reject(new Error("IPC failed"));
			}
			// Other IPCs (get_config, get_stats, etc.) can
			// resolve with a minimal stub so the page's
			// initial-load effects don't trip up the test.
			return Promise.resolve({});
		});

		// The visible MicToggleButton label is the localized
		// ``home.startDictation`` string ("Start dictation" in
		// en, the default locale in tests).
		const toggleBtn = await findByRole("button", {
			name: /start dictation/i,
		});
		toggleBtn.click();

		// The catch block fires ``await``-ed microtasks; let
		// them flush before asserting.
		await vi.waitFor(() => {
			expect(toast.error).toHaveBeenCalledTimes(1);
		});
		// The argument must be the localized ``home.toggleFailed``
		// message — NOT a raw exception string, NOT a generic
		// "Error" label. This guards against a regression where
		// the toast is dropped or replaced with a non-localized
		// fallback.
		const arg = vi.mocked(toast.error).mock.calls[0]?.[0];
		expect(typeof arg).toBe("string");
		expect(arg).not.toBe("");
		// en.json value: "Couldn't toggle dictation. Please try again."
		expect(arg).toMatch(/toggle dictation/i);
	});
});
