import { create } from "zustand";

interface LastResortToastState {
	lastToastedAt: Record<string, number>;
	lastToastShownAt: number | null;
	/** Record that ``backend`` toasted at ``timestamp``. */
	setLastToastedAt: (backend: string, timestamp: number) => void;
	/** Record that a last-resort toast (any backend) showed at ``timestamp``. */
	setLastToastShownAt: (timestamp: number) => void;
	resetLastToastedAt: () => void;
}

export const useLastResortToastStore = create<LastResortToastState>((set) => ({
	lastToastedAt: {},
	lastToastShownAt: null,
	setLastToastedAt: (backend, timestamp) =>
		set((state) => ({
			lastToastedAt: {
				...state.lastToastedAt,
				[backend]: timestamp,
			},
		})),
	setLastToastShownAt: (timestamp) => set({ lastToastShownAt: timestamp }),
	resetLastToastedAt: () => set({ lastToastedAt: {}, lastToastShownAt: null }),
}));
