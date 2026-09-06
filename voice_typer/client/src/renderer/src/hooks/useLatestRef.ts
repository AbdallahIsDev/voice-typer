// Canonical latest-ref mirror, shared app-wide (BP-16 phase 1).
//
// The "latest-ref mirror" keeps a callback/value readable inside effects
// and event handlers without re-running the effect when its identity
// churns:
//
//     const callRef = useLatestRef(call);
//     useEffect(() => { callRef.current(...) }, []);   // stable deps
//
// Previously this two-line pattern was copy-pasted across 22+ files,
// each with its own variant of the same explanatory comment. The
// rationale lives ONCE here:
//
// A ref write never triggers a render, so mirroring into a ref cannot
// re-run effects (the OOM-loop class: an effect that both reads and
// depends on a churning `call` re-runs on every render). Reading
// `ref.current` inside a stale closure still sees the LATEST callback.
// The `useEffect` (not `useLayoutEffect`) timing is sufficient for
// event-handler/effect reads — a handler cannot fire between commit
// and passive-effect flush in a way that observes a torn value, and
// where that subtlety matters, the consuming code reads the value
// during render (not via the mirror).
//
// The AST guard test (`__tests__/useEffect-call-dep-guard.test.ts`)
// pins the inline mirror idiom for `call`-shaped callbacks; consumers
// of this hook carry NO inline mirror and NO `call`-in-deps effect, so
// they satisfy the guard trivially.

import { useEffect, useRef } from "react";

export function useLatestRef<T>(value: T): React.RefObject<T> {
	const ref = useRef(value);
	useEffect(() => {
		ref.current = value;
	}, [value]);
	return ref;
}
