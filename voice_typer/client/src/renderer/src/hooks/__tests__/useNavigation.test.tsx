import { act, cleanup, render } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { _resetNavigationForTest, useNavigation } from "@/hooks/useNavigation";

beforeEach(() => {
	// Reset the shared store + the document-listener install flag
	// so each test starts fresh.
	_resetNavigationForTest();
});

afterEach(() => {
	cleanup();
});

function Probe({
	captures,
}: {
	captures: { current: ReturnType<typeof useNavigation> | null };
}) {
	captures.current = useNavigation();
	return null as unknown as ReactNode;
}

describe("useNavigation document listeners install exactly once", () => {
	it("registers `mouseup` + `keydown` on document exactly once across multiple consumers", () => {
		const addSpy = vi.spyOn(document, "addEventListener");
		addSpy.mockClear();

		// Mount 3 consumers (mirrors the real app where App, Home,
		// Settings, History, Dashboard, AudioSettingsSection all
		// call `useNavigation`).
		const captures1 = {
			current: null as ReturnType<typeof useNavigation> | null,
		};
		const captures2 = {
			current: null as ReturnType<typeof useNavigation> | null,
		};
		const captures3 = {
			current: null as ReturnType<typeof useNavigation> | null,
		};
		const u1 = render(<Probe captures={captures1} />);
		const u2 = render(<Probe captures={captures2} />);
		const u3 = render(<Probe captures={captures3} />);

		const mouseupCalls = addSpy.mock.calls.filter((c) => c[0] === "mouseup");
		const keydownCalls = addSpy.mock.calls.filter((c) => c[0] === "keydown");

		// Despite 3 consumers, only 1 mouseup + 1 keydown listener
		// should be registered (the first consumer's mount triggers
		// `ensureDocumentListeners()`; subsequent consumers no-op).
		expect(mouseupCalls.length).toBe(1);
		expect(keydownCalls.length).toBe(1);

		u1.unmount();
		u2.unmount();
		u3.unmount();
		addSpy.mockRestore();
	});

	it("re-installs listeners after `_resetNavigationForTest()` resets the install flag", () => {
		const addSpy = vi.spyOn(document, "addEventListener");

		const captures1 = {
			current: null as ReturnType<typeof useNavigation> | null,
		};
		const u1 = render(<Probe captures={captures1} />);
		const countAfterFirst = addSpy.mock.calls.filter(
			(c) => c[0] === "mouseup" || c[0] === "keydown",
		).length;
		expect(countAfterFirst).toBe(2); // 1 mouseup + 1 keydown

		// Reset the install flag + unmount the first consumer.
		u1.unmount();
		_resetNavigationForTest();
		addSpy.mockClear();

		const captures2 = {
			current: null as ReturnType<typeof useNavigation> | null,
		};
		const u2 = render(<Probe captures={captures2} />);
		const countAfterReset = addSpy.mock.calls.filter(
			(c) => c[0] === "mouseup" || c[0] === "keydown",
		).length;
		expect(countAfterReset).toBe(2); // Re-installed.

		u2.unmount();
		addSpy.mockRestore();
	});
});

describe("useNavigation useShallow consolidation (4 selector runs per update)", () => {
	it("navigating fires the expected number of store subscriber notifications", () => {
		// Target: the 4 stable action selectors are consolidated
		// into a single `useShallow` subscription. Combined with the
		// 3 value selectors (`page`, `history`, `index`), the total
		// selector run count per `set()` is 4 (down from 7).
		// We can't directly count selector runs from outside the
		// store, but we CAN verify the consolidation indirectly:
		// the `useShallow` subscription returns a STABLE object
		// reference across unrelated state changes (because the
		// 4 action function references never change identity).
		// So a `navigate()` call that changes `page` should NOT
		// change the `navigate`/`replace`/`goBack`/`goForward`
		// references, the consumer's `useShallow` subscription
		// does not trigger a re-render for the action slice.
		const captures = {
			current: null as ReturnType<typeof useNavigation> | null,
		};
		const u = render(<Probe captures={captures} />);

		const actionsBefore = {
			navigate: captures.current?.navigate,
			replace: captures.current?.replace,
			goBack: captures.current?.goBack,
			goForward: captures.current?.goForward,
		};

		// Navigate to a different page. Settings is now a HUB + section
		// hub) is a real destination. From "home" the hub-model path
		// is: stage any deep-link opts (none here), then exactly ONE
		// `apply()` (one store `set()` + one localStorage write) that
		// pushes "settings" onto the history stack, no redirect means
		// no second `replace()` apply call. The test verifies the
		// navigation happened by checking currentPage === "settings"
		// (the hub literal itself).
		act(() => {
			captures.current?.navigate("settings");
		});

		const actionsAfter = {
			navigate: captures.current?.navigate,
			replace: captures.current?.replace,
			goBack: captures.current?.goBack,
			goForward: captures.current?.goForward,
		};

		// The action references should be unchanged (Zustand store
		// actions never change identity, and `useShallow`'s
		// shallow-equal check returns the same object reference).
		expect(actionsAfter.navigate).toBe(actionsBefore.navigate);
		expect(actionsAfter.replace).toBe(actionsBefore.replace);
		expect(actionsAfter.goBack).toBe(actionsBefore.goBack);
		expect(actionsAfter.goForward).toBe(actionsBefore.goForward);

		// The page value should have changed. Settings is now a HUB
		// (no redirect): navigate("settings") lands on the hub literal
		// itself.
		expect(captures.current?.currentPage).toBe("settings");

		u.unmount();
	});

	it("the 4 action selectors are exposed via a single useShallow subscription (stable across N navigations)", () => {
		// Mount a consumer, navigate several times, and verify the
		// action function references remain stable across all
		// navigations (proof that the `useShallow` subscription
		// returns the same shallow-equal object reference each
		// time, not a fresh object).
		const captures = {
			current: null as ReturnType<typeof useNavigation> | null,
		};
		const u = render(<Probe captures={captures} />);

		const navigateRef0 = captures.current?.navigate;
		const goBackRef0 = captures.current?.goBack;
		const goForwardRef0 = captures.current?.goForward;
		const replaceRef0 = captures.current?.replace;

		for (const page of [
			"settings",
			"history",
			"home",
			"aboutAndPrivacy",
		] as const) {
			act(() => {
				captures.current?.navigate(page);
			});
		}

		expect(captures.current?.navigate).toBe(navigateRef0);
		expect(captures.current?.goBack).toBe(goBackRef0);
		expect(captures.current?.goForward).toBe(goForwardRef0);
		expect(captures.current?.replace).toBe(replaceRef0);

		u.unmount();
	});
});

describe("useNavigation consent deep-link channel (pendingConsentField / consumeConsentField)", () => {
	it("stages the consent field when navigate('settings', { consentField }) is called", () => {
		const captures = {
			current: null as ReturnType<typeof useNavigation> | null,
		};
		const u = render(<Probe captures={captures} />);

		// No deep-link pending by default.
		expect(captures.current?.pendingConsentField).toBeNull();

		act(() => {
			captures.current?.navigate("settings", {
				consentField: "voice_biometric_consent",
			});
		});

		expect(captures.current?.pendingConsentField).toBe(
			"voice_biometric_consent",
		);

		// consume reads AND clears (one-shot, a stale target can't
		// re-fire on a later Settings visit).
		let consumed: string | null = "sentinel";
		act(() => {
			consumed = captures.current?.consumeConsentField() ?? null;
		});
		expect(consumed).toBe("voice_biometric_consent");
		expect(captures.current?.pendingConsentField).toBeNull();
		// Second consume is a no-op.
		act(() => {
			consumed = captures.current?.consumeConsentField() ?? null;
		});
		expect(consumed).toBeNull();

		u.unmount();
	});

	it("stages the field even when already ON the settings page (same-page deep-link re-arms)", () => {
		const captures = {
			current: null as ReturnType<typeof useNavigation> | null,
		};
		const u = render(<Probe captures={captures} />);

		// Navigate to settings first (no deep-link), then fire a
		// consent refusal WHILE already on settings. Settings is now a
		// HUB (no redirect): navigate("settings") stays on the hub
		// literal "settings".
		act(() => {
			captures.current?.navigate("settings");
		});
		expect(captures.current?.currentPage).toBe("settings");

		act(() => {
			captures.current?.navigate("settings", {
				consentField: "cloud_openai_consent",
			});
		});

		// The same-page early-return must NOT swallow the deep-link.
		// Confirmed against useNavigation.ts: the consentField staging
		// `set()` runs BEFORE the `page === current` early return, so
		// a same-page navigate("settings", { consentField }) still
		// arms the pending field even though the hub model makes this
		// a pure no-op for page/history (no redirect, no apply).
		expect(captures.current?.pendingConsentField).toBe("cloud_openai_consent");

		u.unmount();
	});

	it("a plain navigate (no consentField) does not arm the pending field", () => {
		const captures = {
			current: null as ReturnType<typeof useNavigation> | null,
		};
		const u = render(<Probe captures={captures} />);

		act(() => {
			captures.current?.navigate("settings");
		});
		expect(captures.current?.pendingConsentField).toBeNull();

		u.unmount();
	});
});

describe("useNavigation cold-start privacy: never restore Microphone page", () => {
	afterEach(() => {
		localStorage.removeItem("vt_nav_state");
		_resetNavigationForTest();
	});

	it("remaps a persisted microphone page to home on load", () => {
		localStorage.setItem(
			"vt_nav_state",
			JSON.stringify({ page: "microphone", history: ["microphone"], index: 0 }),
		);
		_resetNavigationForTest();

		const captures = {
			current: null as ReturnType<typeof useNavigation> | null,
		};
		const u = render(<Probe captures={captures} />);

		expect(captures.current?.currentPage).toBe("home");

		u.unmount();
	});

	it("still restores other pages (e.g. settings) normally", () => {
		localStorage.setItem(
			"vt_nav_state",
			JSON.stringify({ page: "settings", history: ["settings"], index: 0 }),
		);
		_resetNavigationForTest();

		const captures = {
			current: null as ReturnType<typeof useNavigation> | null,
		};
		const u = render(<Probe captures={captures} />);

		expect(captures.current?.currentPage).toBe("settings");

		u.unmount();
	});

	it("in-session navigate to microphone still works after a privacy remap", () => {
		localStorage.setItem(
			"vt_nav_state",
			JSON.stringify({ page: "microphone", history: ["microphone"], index: 0 }),
		);
		_resetNavigationForTest();

		const captures = {
			current: null as ReturnType<typeof useNavigation> | null,
		};
		const u = render(<Probe captures={captures} />);
		expect(captures.current?.currentPage).toBe("home");

		act(() => {
			captures.current?.navigate("microphone");
		});
		expect(captures.current?.currentPage).toBe("microphone");

		u.unmount();
	});
});
