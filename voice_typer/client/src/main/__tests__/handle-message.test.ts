// @vitest-environment node
/**
 * : behavioral tests for `src/main/python/handle-message.ts`.
 *
 * Covers the push-event routing surface of `handleMessage()`:
 *   - Reply resolution: a message with a numeric `id` resolves the
 *     matching entry in `state.pendingRequests` (and deletes the entry
 *     so it can't be resolved twice).
 *   - Reply rejection: a reply with `type === "error"` rejects the
 *     pending entry with a structured `Error` carrying the `code`
 *     field so the renderer can branch on failure class.
 *   - Push events: `bubble_show` / `bubble_hide` route to the bubble
 *     window; `quit_app` calls `app.quit()`; `relaunch_app` calls
 *     `relaunchApp()`; `show_window` calls `showMainWindow()`.
 *
 * The existing `structured-logger.test.ts` only inspects the
 * source for log usage — it doesn't exercise the routing behavior.
 * This file complements it with behavioral coverage of the 8+ push
 * event types listed in
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { APP_NAME } from "../branding";

// Mock ``electron.Notification`` instances so tests can assert the
// native-toast behavior (title/body, click handler, duration auto-close)
// without a real OS notification surface. ``clickHandlers`` records the
// ``click`` handler per instance so a test can invoke it directly.
const mockNotifications: Array<{
	opts: { title: string; body: string };
	show: ReturnType<typeof vi.fn>;
	close: ReturnType<typeof vi.fn>;
	clickHandlers: Array<() => void>;
}> = [];

const mocks = vi.hoisted(() => {
	class MockNotification {
		static isSupported = vi.fn(() => true);
		public opts: { title: string; body: string };
		public show = vi.fn();
		public close = vi.fn();
		public clickHandlers: Array<() => void> = [];
		constructor(opts: { title: string; body: string }) {
			this.opts = opts;
			mockNotifications.push({
				opts: this.opts,
				show: this.show,
				close: this.close,
				clickHandlers: this.clickHandlers,
			});
		}
		on(event: string, handler: () => void) {
			if (event === "click") this.clickHandlers.push(handler);
		}
	}

	return {
		// Window-control stubs.
		showBubbleWindow: vi.fn(),
		hideBubbleWindow: vi.fn(),
		showMainWindow: vi.fn(),
		Notification: MockNotification,
		// Electron app.quit / relaunchApp stubs.
		appQuit: vi.fn(),
		relaunchApp: vi.fn(),
		// sendToPython stub (for the relaunch_ack reply).
		sendToPython: vi.fn(() => Promise.resolve()),
		// broadcastToMainWindow stub (so we can assert the event was
		// forwarded to the main window with the session nonce attached).
		broadcastToMainWindow: vi.fn(),
		// bubbleWindow.webContents.send stub.
		bubbleWebContentsSend: vi.fn(),
	};
});

vi.mock("electron", () => ({
	app: { quit: mocks.appQuit },
	Notification: mocks.Notification,
}));

vi.mock("../logging", () => ({
	BUBBLE_CLR: "",
	RESET: "",
	ts: () => "[mock-ts]",
	log: {
		warn: vi.fn(),
		info: vi.fn(),
		error: vi.fn(),
		debug: vi.fn(),
	},
}));

vi.mock("../state", () => ({
	state: {
		pendingRequests: new Map<
			number,
			{ resolve: (v: unknown) => void; reject: (e: unknown) => void }
		>(),
		bubbleWindow: {
			isDestroyed: () => false,
			webContents: { send: mocks.bubbleWebContentsSend },
		},
		mainWindow: null,
		sessionNonce: "test-nonce-123",
		_relaunching: false,
	},
}));

vi.mock("../windows", () => ({
	showBubbleWindow: mocks.showBubbleWindow,
	hideBubbleWindow: mocks.hideBubbleWindow,
	showMainWindow: mocks.showMainWindow,
}));

vi.mock("../windows/main-window", () => ({
	broadcastToMainWindow: mocks.broadcastToMainWindow,
}));

vi.mock("../python/relaunch-app", () => ({
	relaunchApp: mocks.relaunchApp,
}));

vi.mock("../python/send-to-python", () => ({
	sendToPython: mocks.sendToPython,
}));

import {
	_resetLastKnownBubbleMode,
	getLastKnownBubbleMode,
} from "../ipc/bubble-handlers";
import { handleMessage } from "../python/handle-message";
import { state } from "../state";

describe("XS-78: handle-message.ts", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		mockNotifications.length = 0;
		state.pendingRequests.clear();
		_resetLastKnownBubbleMode();
		// ``vi.clearAllMocks`` clears call history but NOT the
		// ``isSupported`` implementation; restore the default explicitly
		// so tests that set it to false don't leak into later tests.
		(
			mocks.Notification.isSupported as ReturnType<typeof vi.fn>
		).mockImplementation(() => true);
	});

	describe("reply resolution (numeric msg.id)", () => {
		it("resolves the matching pendingRequests entry with msg.data", async () => {
			const resolve = vi.fn();
			const reject = vi.fn();
			state.pendingRequests.set(42, { resolve, reject });

			handleMessage({ id: 42, type: "result", data: { ok: true } });

			expect(resolve).toHaveBeenCalledWith({ ok: true });
			expect(reject).not.toHaveBeenCalled();
			// Entry must be deleted so a duplicate reply can't resolve twice.
			expect(state.pendingRequests.has(42)).toBe(false);
		});

		it("rejects the pending entry when type === 'error' and attaches the code field", async () => {
			const resolve = vi.fn();
			const reject = vi.fn();
			state.pendingRequests.set(7, { resolve, reject });

			handleMessage({
				id: 7,
				type: "error",
				data: {
					message: "Rate limit exceeded",
					code: "rate_limited",
				},
			});

			expect(reject).toHaveBeenCalledTimes(1);
			expect(resolve).not.toHaveBeenCalled();
			const err = reject.mock.calls[0]?.[0] as Error;
			expect(err).toBeInstanceOf(Error);
			expect(err.message).toBe("Rate limit exceeded");
			//the structured `code` field must be attached
			// so the renderer can branch on failure class without
			// pattern-matching the human-readable message.
			expect((err as Error & { code?: string }).code).toBe("rate_limited");
		});

		it("falls back to 'Unknown error' when errData.message is missing", async () => {
			const resolve = vi.fn();
			const reject = vi.fn();
			state.pendingRequests.set(8, { resolve, reject });

			handleMessage({ id: 8, type: "error", data: {} });

			const err = reject.mock.calls[0]?.[0] as Error;
			expect(err.message).toBe("Unknown error");
		});

		it("is a no-op when the pending entry is missing (stale reply)", () => {
			// No entry for id=999 — must not throw.
			expect(() =>
				handleMessage({ id: 999, type: "result", data: "late" }),
			).not.toThrow();
		});
	});

	describe("push events (no msg.id)", () => {
		it("bubble_show → showBubbleWindow()", () => {
			handleMessage({ type: "bubble_show" });
			expect(mocks.showBubbleWindow).toHaveBeenCalledTimes(1);
		});

		it("bubble_hide → hideBubbleWindow()", () => {
			handleMessage({ type: "bubble_hide" });
			expect(mocks.hideBubbleWindow).toHaveBeenCalledTimes(1);
		});

		it("bubble_set_state → bubbleWindow.webContents.send('bubble:set-state', state)", () => {
			handleMessage({ type: "bubble_set_state", data: { state: "listening" } });
			expect(mocks.bubbleWebContentsSend).toHaveBeenCalledWith(
				"bubble:set-state",
				"listening",
			);
			expect(getLastKnownBubbleMode()).toBe("listening");
		});

		it("bubble_set_state forwards the full object payload when it carries message/transcript", () => {
			handleMessage({
				type: "bubble_set_state",
				data: {
					state: "transcribing",
					message: undefined,
					transcript: "hello world",
				},
			});
			expect(mocks.bubbleWebContentsSend).toHaveBeenCalledWith(
				"bubble:set-state",
				{
					state: "transcribing",
					message: undefined,
					transcript: "hello world",
				},
			);
			expect(getLastKnownBubbleMode()).toBe("transcribing");
		});

		it("bubble_level → bubbleWindow.webContents.send('bubble:level', data)", () => {
			const data = { rms: 0.5, peak: 0.9 };
			handleMessage({ type: "bubble_level", data });
			expect(mocks.bubbleWebContentsSend).toHaveBeenCalledWith(
				"bubble:level",
				data,
			);
		});

		it("bubble_config → bubbleWindow.webContents.send('bubble:config', data)", () => {
			const data = { bubble_mic_button: true };
			handleMessage({ type: "bubble_config", data });
			expect(mocks.bubbleWebContentsSend).toHaveBeenCalledWith(
				"bubble:config",
				data,
			);
		});

		it("show_window → showMainWindow()", () => {
			handleMessage({ type: "show_window" });
			expect(mocks.showMainWindow).toHaveBeenCalledTimes(1);
		});

		it("quit_app → app.quit()", () => {
			handleMessage({ type: "quit_app" });
			expect(mocks.appQuit).toHaveBeenCalledTimes(1);
		});

		it("relaunch_app → sendToPython(relaunch_ack) + relaunchApp()", () => {
			handleMessage({ type: "relaunch_app" });
			// PERF-005: ack is sent BEFORE relaunchApp() tears down the
			// socket so the server can drop its fixed 300ms sleep.
			expect(mocks.sendToPython).toHaveBeenCalledWith({ type: "relaunch_ack" });
			expect(mocks.relaunchApp).toHaveBeenCalledTimes(1);
		});

		describe("notification → native toast with click handler (Models-page pointer)", () => {
			it("shows a native Notification with title/body from the payload", () => {
				handleMessage({
					type: "notification",
					data: {
						title: APP_NAME,
						message:
							"Model not downloaded yet. Open the Models page and download.",
						duration_ms: 0,
						critical: false,
						click_path: "/models",
					},
				});

				expect(mockNotifications).toHaveLength(1);
				const notif = mockNotifications[0];
				if (!notif) throw new Error("expected a created Notification");
				expect(notif.opts).toEqual({
					title: APP_NAME,
					body: "Model not downloaded yet. Open the Models page and download.",
				});
				expect(notif.show).toHaveBeenCalledTimes(1);
			});

			it("registers a click handler when click_path is present", () => {
				handleMessage({
					type: "notification",
					data: {
						title: APP_NAME,
						message: "backend unloaded",
						click_path: "/models",
					},
				});

				const notif = mockNotifications[0];
				if (!notif) throw new Error("expected a created Notification");
				expect(notif.clickHandlers).toHaveLength(1);

				notif.clickHandlers[0]?.();

				// Click must show the main window and broadcast a
				// ``navigate`` event pointing at the Models page (the
				// renderer's existing ``navigate`` listener routes there).
				expect(mocks.showMainWindow).toHaveBeenCalledTimes(1);
				expect(mocks.broadcastToMainWindow).toHaveBeenCalledWith(
					"python-event",
					expect.objectContaining({
						type: "navigate",
						data: { path: "/models" },
					}),
				);
			});

			it("click with click_consent_field broadcasts navigate /settings + consent_field (a consent-gate toast)", () => {
				handleMessage({
					type: "notification",
					data: {
						title: APP_NAME,
						message: "Voice biometric consent is required to start recording.",
						click_consent_field: "voice_biometric_consent",
					},
				});

				const notif = mockNotifications[0];
				if (!notif) throw new Error("expected a created Notification");
				expect(notif.clickHandlers).toHaveLength(1);

				notif.clickHandlers[0]?.();

				// Clicking a consent-gate toast opens the main window and
				// deep-links to the EXACT Settings consent row — App.tsx's
				// navigate handler forwards ``consent_field`` to Settings'
				// pendingConsentField deep-link (scroll-to + highlight).
				expect(mocks.showMainWindow).toHaveBeenCalledTimes(1);
				expect(mocks.broadcastToMainWindow).toHaveBeenCalledWith(
					"python-event",
					expect.objectContaining({
						type: "navigate",
						data: {
							path: "/settings",
							consent_field: "voice_biometric_consent",
						},
					}),
				);
			});

			it("click_consent_field takes precedence over click_path when both are present", () => {
				handleMessage({
					type: "notification",
					data: {
						title: APP_NAME,
						message: "consent gate",
						click_path: "/models",
						click_consent_field: "cloud_groq_consent",
					},
				});

				const notif = mockNotifications[0];
				if (!notif) throw new Error("expected a created Notification");
				notif.clickHandlers[0]?.();

				// The click must broadcast navigate to the CONSENT row
				// (path:/settings + consent_field), not the click_path
				// target (/models). ``toHaveBeenCalledWith`` matches ANY
				// broadcast (the raw notification event is also
				// broadcast), and the objectContaining data constraint
				// proves only the consent-row navigate exists — a
				// navigate to /models would NOT match this assertion.
				expect(mocks.broadcastToMainWindow).toHaveBeenCalledWith(
					"python-event",
					expect.objectContaining({
						type: "navigate",
						data: {
							path: "/settings",
							consent_field: "cloud_groq_consent",
						},
					}),
				);
			});

			it("attaches the SEC-029 session nonce to the synthetic navigate event", () => {
				handleMessage({
					type: "notification",
					data: { title: APP_NAME, message: "m", click_path: "/models" },
				});

				const notif = mockNotifications[0];
				if (!notif) throw new Error("expected a created Notification");
				notif.clickHandlers[0]?.();

				const [, msg] = mocks.broadcastToMainWindow.mock.calls[0] ?? [];
				expect((msg as Record<string, unknown>)._session_nonce).toBe(
					"test-nonce-123",
				);
			});

			it("does not register a click handler when click_path is absent", () => {
				handleMessage({
					type: "notification",
					data: { title: APP_NAME, message: "plain notice" },
				});

				const notif = mockNotifications[0];
				if (!notif) throw new Error("expected a created Notification");
				expect(notif.clickHandlers).toHaveLength(0);
			});

			it("auto-closes after duration_ms when set", () => {
				vi.useFakeTimers();
				try {
					handleMessage({
						type: "notification",
						data: { title: "t", message: "m", duration_ms: 5000 },
					});

					const notif = mockNotifications[0];
					if (!notif) throw new Error("expected a created Notification");
					expect(notif.close).not.toHaveBeenCalled();
					vi.advanceTimersByTime(5000);
					expect(notif.close).toHaveBeenCalledTimes(1);
				} finally {
					vi.useRealTimers();
				}
			});

			it("skips the native toast when Notification.isSupported() is false but still broadcasts", () => {
				(
					mocks.Notification.isSupported as ReturnType<typeof vi.fn>
				).mockImplementation(() => false);

				handleMessage({
					type: "notification",
					data: { title: APP_NAME, message: "m", click_path: "/models" },
				});

				// No native toast created…
				expect(mockNotifications).toHaveLength(0);
				// …but the event still falls through to the renderer
				// broadcast (in-app toast consumers keep working).
				expect(mocks.broadcastToMainWindow).toHaveBeenCalledWith(
					"python-event",
					expect.objectContaining({ type: "notification" }),
				);
			});

			it("skips empty title/body payloads without creating a toast", () => {
				handleMessage({ type: "notification", data: {} });
				expect(mockNotifications).toHaveLength(0);
			});
		});
	});

	describe("SEC-029: session nonce injection", () => {
		it("attaches state.sessionNonce to push events that don't already carry one", () => {
			handleMessage({ type: "show_window" });
			const forwarded = mocks.broadcastToMainWindow.mock.calls[0];
			if (!forwarded) throw new Error("Expected forwarded call");
			const [, msg] = forwarded;
			expect((msg as Record<string, unknown>)._session_nonce).toBe(
				"test-nonce-123",
			);
		});
	});

	describe("SEC-017: every push event is forwarded to the main window", () => {
		it("broadcastToMainWindow('python-event', msg) is called for every push event", () => {
			handleMessage({ type: "show_window" });
			expect(mocks.broadcastToMainWindow).toHaveBeenCalledWith(
				"python-event",
				expect.objectContaining({ type: "show_window" }),
			);
		});
	});
});
