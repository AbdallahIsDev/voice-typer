/**
 * Focused unit tests for the `useBubbleBridge` hook.
 *
 * Background
 * ----------
 * Pre-refactor, the bubble package registered 11 separate IPC
 * listeners across 3 hooks + 1 component
 * (`useBubbleLifecycle` × 2, `useBubbleStateMachine` × 3,
 * `useAudioLevels` × 3, `useThemeSync` × 1, `Bubble.tsx` × 2). Each
 * subscription was a separate Electron IPC listener on the
 * BrowserWindow's `webContents`.
 *
 * Post-refactor, the bridge centralises the subscriptions into ONE
 * listener per event channel; consumers register handlers via
 * `bridge.on(event, handler)`.
 *
 * These tests verify:
 *   1. The bridge registers EXACTLY ONE listener per event channel
 *      on `window.bubble` (not 11).
 *   2. The bridge fans out events to ALL registered handlers.
 *   3. `setLevelActive(true/false)` toggles the underlying
 *      `api.onLevel` IPC subscription on/off.
 *   4. The bridge unsubscribes all IPC listeners on unmount.
 */
import { act, cleanup, render } from "@testing-library/react";
import { useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BubbleBridgeProvider, useBubbleBridge } from "../useBubbleBridge";

// ── Mock window.bubble API ──────────────────────────────────────────
function makeMockBubble() {
	const listeners: {
		show: Array<() => void>;
		hide: Array<() => void>;
		setState: Array<(s: unknown) => void>;
		config: Array<(c: Record<string, unknown>) => void>;
		level: Array<(d: { rms: number; peak: number }) => void>;
		draggable: Array<(d: boolean) => void>;
	} = {
		show: [],
		hide: [],
		setState: [],
		config: [],
		level: [],
		draggable: [],
	};
	return {
		onShow: vi.fn((cb: () => void) => {
			listeners.show.push(cb);
			return () => {
				listeners.show = listeners.show.filter((l) => l !== cb);
			};
		}),
		onHide: vi.fn((cb: () => void) => {
			listeners.hide.push(cb);
			return () => {
				listeners.hide = listeners.hide.filter((l) => l !== cb);
			};
		}),
		onSetState: vi.fn((cb: (s: unknown) => void) => {
			listeners.setState.push(cb);
			return () => {
				listeners.setState = listeners.setState.filter((l) => l !== cb);
			};
		}),
		onConfig: vi.fn((cb: (c: Record<string, unknown>) => void) => {
			listeners.config.push(cb);
			return () => {
				listeners.config = listeners.config.filter((l) => l !== cb);
			};
		}),
		onLevel: vi.fn((cb: (d: { rms: number; peak: number }) => void) => {
			listeners.level.push(cb);
			return () => {
				listeners.level = listeners.level.filter((l) => l !== cb);
			};
		}),
		onDraggable: vi.fn((cb: (d: boolean) => void) => {
			listeners.draggable.push(cb);
			return () => {
				listeners.draggable = listeners.draggable.filter((l) => l !== cb);
			};
		}),
		_listeners: listeners,
	};
}

type MockBubble = ReturnType<typeof makeMockBubble>;

let mockBubble: MockBubble;

beforeEach(() => {
	mockBubble = makeMockBubble();
	(window as unknown as Record<string, unknown>).bubble = mockBubble;
});

afterEach(() => {
	cleanup();
	delete (window as unknown as Record<string, unknown>).bubble;
});

// A wrapper component that uses useEffect to register handlers on the
// bridge. Each handler prop is optional so a test can register only
// the events it cares about. `levelActive` toggles the dynamic
// `onLevel` IPC subscription via `bridge.setLevelActive(boolean)`.
function ConsumerWithEffects(props: {
	onShow?: () => void;
	onHide?: () => void;
	onSetState?: (s: unknown) => void;
	onConfig?: (c: Record<string, unknown>) => void;
	onLevel?: (d: { rms: number; peak: number }) => void;
	onDraggable?: (d: boolean) => void;
	levelActive?: boolean;
}) {
	const bridge = useBubbleBridge();
	const {
		onShow,
		onHide,
		onSetState,
		onConfig,
		onLevel,
		onDraggable,
		levelActive,
	} = props;
	useEffect(() => {
		if (!bridge) return;
		const offs: Array<() => void> = [];
		if (onShow) offs.push(bridge.on("show", onShow));
		if (onHide) offs.push(bridge.on("hide", onHide));
		if (onSetState) offs.push(bridge.on("setState", onSetState));
		if (onConfig) offs.push(bridge.on("config", onConfig));
		if (onLevel) offs.push(bridge.on("level", onLevel));
		if (onDraggable) offs.push(bridge.on("draggable", onDraggable));
		return () => {
			for (const off of offs) off();
		};
	}, [bridge, onShow, onHide, onSetState, onConfig, onLevel, onDraggable]);
	useEffect(() => {
		if (!bridge) return;
		if (levelActive !== undefined) {
			bridge.setLevelActive(levelActive);
		}
	}, [bridge, levelActive]);
	return null;
}

describe("useBubbleBridge: centralised IPC subscriptions", () => {
	it("registers EXACTLY ONE listener per event channel on window.bubble", () => {
		render(
			<BubbleBridgeProvider>
				<ConsumerWithEffects
					onShow={vi.fn()}
					onHide={vi.fn()}
					onSetState={vi.fn()}
					onConfig={vi.fn()}
					onLevel={vi.fn()}
					onDraggable={vi.fn()}
				/>
			</BubbleBridgeProvider>,
		);

		// Each event channel should have exactly ONE IPC listener
		// (the bridge's), regardless of how many consumers
		// registered handlers via bridge.on(...).
		expect(mockBubble.onShow).toHaveBeenCalledTimes(1);
		expect(mockBubble.onHide).toHaveBeenCalledTimes(1);
		expect(mockBubble.onSetState).toHaveBeenCalledTimes(1);
		expect(mockBubble.onConfig).toHaveBeenCalledTimes(1);
		expect(mockBubble.onDraggable).toHaveBeenCalledTimes(1);
		// onLevel is gated by setLevelActive; without a consumer
		// calling setLevelActive(true), the bridge does NOT
		// subscribe to onLevel.
		expect(mockBubble.onLevel).toHaveBeenCalledTimes(0);
	});

	it("fans out events to ALL registered handlers", () => {
		const showA = vi.fn();
		const showB = vi.fn();
		const configA = vi.fn();
		const configB = vi.fn();

		function MultiConsumer() {
			const bridge = useBubbleBridge();
			useEffect(() => {
				if (!bridge) return;
				const offs = [
					bridge.on("show", showA),
					bridge.on("show", showB),
					bridge.on("config", configA),
					bridge.on("config", configB),
				];
				return () => {
					for (const off of offs) off();
				};
			}, [bridge]);
			return null;
		}

		render(
			<BubbleBridgeProvider>
				<MultiConsumer />
			</BubbleBridgeProvider>,
		);

		// Drive the show event — both handlers should fire.
		act(() => {
			for (const cb of mockBubble._listeners.show) cb();
		});
		expect(showA).toHaveBeenCalledTimes(1);
		expect(showB).toHaveBeenCalledTimes(1);

		// Drive the config event — both handlers should fire.
		act(() => {
			for (const cb of mockBubble._listeners.config) {
				cb({ theme_mode: "dark" });
			}
		});
		expect(configA).toHaveBeenCalledTimes(1);
		expect(configB).toHaveBeenCalledTimes(1);
		expect(configA).toHaveBeenCalledWith({ theme_mode: "dark" });
		expect(configB).toHaveBeenCalledWith({ theme_mode: "dark" });
	});

	it("setLevelActive(true) subscribes to onLevel; setLevelActive(false) unsubscribes", () => {
		const onLevel = vi.fn();
		const { rerender } = render(
			<BubbleBridgeProvider>
				<ConsumerWithEffects onLevel={onLevel} levelActive={true} />
			</BubbleBridgeProvider>,
		);

		// After mount with levelActive=true, the bridge should
		// have subscribed to onLevel exactly once.
		expect(mockBubble.onLevel).toHaveBeenCalledTimes(1);
		expect(mockBubble._listeners.level.length).toBe(1);

		// Drive a level event — the handler should fire.
		act(() => {
			for (const cb of mockBubble._listeners.level) {
				cb({ rms: 0.5, peak: 0.7 });
			}
		});
		expect(onLevel).toHaveBeenCalledWith({ rms: 0.5, peak: 0.7 });

		// Now toggle levelActive to false — the bridge should
		// unsubscribe from onLevel.
		rerender(
			<BubbleBridgeProvider>
				<ConsumerWithEffects onLevel={onLevel} levelActive={false} />
			</BubbleBridgeProvider>,
		);
		expect(mockBubble._listeners.level.length).toBe(0);

		// Driving a level event now should NOT fire the handler
		// (no listener registered on the api).
		onLevel.mockClear();
		act(() => {
			for (const cb of mockBubble._listeners.level) {
				cb({ rms: 0.9, peak: 0.99 });
			}
		});
		expect(onLevel).not.toHaveBeenCalled();
	});

	it("unsubscribes ALL IPC listeners on unmount", () => {
		const { unmount } = render(
			<BubbleBridgeProvider>
				<ConsumerWithEffects
					onShow={vi.fn()}
					onHide={vi.fn()}
					onSetState={vi.fn()}
					onConfig={vi.fn()}
					onLevel={vi.fn()}
					onDraggable={vi.fn()}
					levelActive={true}
				/>
			</BubbleBridgeProvider>,
		);

		// Before unmount, all 6 channels are subscribed.
		expect(mockBubble._listeners.show.length).toBe(1);
		expect(mockBubble._listeners.hide.length).toBe(1);
		expect(mockBubble._listeners.setState.length).toBe(1);
		expect(mockBubble._listeners.config.length).toBe(1);
		expect(mockBubble._listeners.draggable.length).toBe(1);
		expect(mockBubble._listeners.level.length).toBe(1);

		unmount();

		// After unmount, ALL channels are unsubscribed.
		expect(mockBubble._listeners.show.length).toBe(0);
		expect(mockBubble._listeners.hide.length).toBe(0);
		expect(mockBubble._listeners.setState.length).toBe(0);
		expect(mockBubble._listeners.config.length).toBe(0);
		expect(mockBubble._listeners.draggable.length).toBe(0);
		expect(mockBubble._listeners.level.length).toBe(0);
	});
});
