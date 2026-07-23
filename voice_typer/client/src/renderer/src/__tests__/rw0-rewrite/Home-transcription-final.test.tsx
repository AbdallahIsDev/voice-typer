/**
 * RW-0 vitest rewrite — behavioral test for `Home.tsx` listener count.
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

vi.mock("@hugeicons/core-free-icons", () => {
	const make = (name: string) => ({ name });
	return {
		Copy01Icon: make("Copy01Icon"),
		Delete01Icon: make("Delete01Icon"),
		Mic02Icon: make("Mic02Icon"),
		RefreshIcon: make("RefreshIcon"),
		Share08Icon: make("Share08Icon"),
		StarIcon: make("StarIcon"),
		StopIcon: make("StopIcon"),
		TextIcon: make("TextIcon"),
		Tick02Icon: make("Tick02Icon"),
		Time02Icon: make("Time02Icon"),
	};
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
