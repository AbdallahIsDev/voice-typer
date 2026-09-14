import { useCallback, useEffect, useRef, useState } from "react";
import { useLatestRef } from "@/hooks/useLatestRef";
import { usePython } from "@/hooks/usePython";
import { TEST_HOTKEY_TIMEOUT_MS } from "../lib/constants";
import type { PermissionsTestState } from "../lib/types";

export interface UsePermissionsResult {
	permissionsTest: PermissionsTestState;
	handleTestHotkey: () => void;
}

/**
 * Owns the in-wizard "Test hotkey" listener + timeout
 * (ref-tracked so cleanup is deterministic, Fix 9 / Fix 10 contract).
 *
 * The 4-step onboarding flow has no dedicated OS-permissions step
 * (keyboard monitoring is standard app behavior, not a consent gate;
 * macOS Accessibility / Linux input-group setup is surfaced by the
 * Dashboard / Settings KeyboardPermissionBanner), so the previous
 * auto-probe effect (`onboarding_check_permissions` on step entry)
 * and its re-probe callback were removed together with the step.
 */
export function usePermissionsProbe(
	selectedHotkey: string,
): UsePermissionsResult {
	const { call } = usePython();
	// callRef mirror (Home.tsx pattern): keeps the hook render-stable
	// when a test mock hands out a fresh `call` identity per render.
	// The probe effect is gone; the mirror documents the same
	// contract the test-hotkey listener used to share with it.
	const callRef = useLatestRef(call);
	void callRef;

	const [permissionsTest, setPermissionsTest] = useState<PermissionsTestState>({
		kind: "idle",
	});

	// Fix 9: keydown listener + timeout stored in refs so the effect
	// cleanup can tear them down deterministically (was leaking on unmount).
	const permissionsTestTimeoutRef = useRef<
		ReturnType<typeof setTimeout> | undefined
	>(undefined);
	const permissionsTestKeydownRef = useRef<((e: KeyboardEvent) => void) | null>(
		null,
	);

	useEffect(() => {
		return () => {
			if (permissionsTestTimeoutRef.current) {
				clearTimeout(permissionsTestTimeoutRef.current);
				permissionsTestTimeoutRef.current = undefined;
			}
			if (permissionsTestKeydownRef.current) {
				window.removeEventListener(
					"keydown",
					permissionsTestKeydownRef.current,
				);
				permissionsTestKeydownRef.current = null;
			}
		};
	}, []);

	const normalizeHotkey = useCallback((raw: string): string => {
		return raw.replace(/[<>]/g, "").replace(/_/g, "").toLowerCase();
	}, []);

	// ── Test hotkey handler (Fix 9: ref-tracked listener; Fix 10: 10s) ─
	const handleTestHotkey = useCallback(() => {
		if (permissionsTest.kind === "listening") return;
		setPermissionsTest({ kind: "listening" });
		const target = normalizeHotkey(selectedHotkey);
		const onKeyDown = (e: KeyboardEvent) => {
			const pressed = normalizeHotkey(e.key);
			if (pressed && pressed === target) {
				window.removeEventListener("keydown", onKeyDown);
				if (permissionsTestKeydownRef.current === onKeyDown)
					permissionsTestKeydownRef.current = null;
				if (permissionsTestTimeoutRef.current) {
					clearTimeout(permissionsTestTimeoutRef.current);
					permissionsTestTimeoutRef.current = undefined;
				}
				setPermissionsTest({ kind: "success" });
			}
		};
		permissionsTestKeydownRef.current = onKeyDown;
		window.addEventListener("keydown", onKeyDown);
		permissionsTestTimeoutRef.current = setTimeout(() => {
			window.removeEventListener("keydown", onKeyDown);
			if (permissionsTestKeydownRef.current === onKeyDown)
				permissionsTestKeydownRef.current = null;
			setPermissionsTest({ kind: "failure" });
			permissionsTestTimeoutRef.current = undefined;
		}, TEST_HOTKEY_TIMEOUT_MS);
	}, [normalizeHotkey, selectedHotkey, permissionsTest.kind]);

	return {
		permissionsTest,
		handleTestHotkey,
	};
}
