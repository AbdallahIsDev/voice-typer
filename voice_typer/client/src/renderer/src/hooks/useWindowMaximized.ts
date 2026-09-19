import { useEffect, useState } from "react";
import type { WindowBridge } from "@/types/ipc";

export function useWindowMaximized(bridge: WindowBridge | undefined): boolean {
	const [isMaximized, setIsMaximized] = useState(false);

	useEffect(() => {
		if (!bridge) return;
		let cancelled = false;
		bridge
			.isMaximized()
			.then((v) => {
				if (!cancelled) {
					setIsMaximized(v);
					document.documentElement.classList.toggle("is-maximized", v);
				}
			})
			.catch((err) =>
				console.warn(
					"[renderer:useWindowMaximized] window isMaximized failed:",
					err,
				),
			);
		const unsub = bridge.onMaximizedChanged((v) => {
			if (!cancelled) {
				setIsMaximized(v);
				document.documentElement.classList.toggle("is-maximized", v);
			}
		});
		return () => {
			cancelled = true;
			unsub();
		};
	}, [bridge]);

	return isMaximized;
}
