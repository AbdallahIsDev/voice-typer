import { describe, expect, it } from "vitest";
import type { RecordingState } from "@/types/ipc";

// ── Type-level helpers ─────────────────────────────────────────────
// `IsExact<A, B>` is true iff A and B are the same type (no extra or
// missing members).  The standard trick: two conditional types that
// only reduce to `1` (vs `2`) when the candidate type is exactly the
// target, if either side has extra members the trick fails.
type IsExact<A, B> =
	(<T>() => T extends A ? 1 : 2) extends <T>() => T extends B ? 1 : 2
		? true
		: false;

// `HasDead<S>` is true iff at least one of the dead-state literals is
// a member of RecordingState.  We distribute over the dead-state
// union so `HasDead<"listening" | "paused">` is `true | false` (which
// collapses to `boolean`) when EITHER is present.
type DeadStates =
	| "listening"
	| "processing"
	| "warming_up"
	| "downloading"
	| "paused"
	| "setup"
	| "not_configured";

type HasDead<S extends string> = S extends RecordingState ? true : false;

type AnyDead = HasDead<DeadStates>;

// ── Compile-time-checked runtime bindings ──────────────────────────
// Each `const` below has a literal-`true` annotation.  If the type
// on the left of `=` doesn't reduce to `true`, the file fails to
// compile (caught by `tsc --noEmit`).
const _isExact: IsExact<
	RecordingState,
	"idle" | "recording" | "transcribing" | "loading" | "cancelling" | "error"
> = true;

// `AnyDead` is `boolean` (true | false) when at least one dead state
// is present, otherwise `false`.  Narrow it to `false` to assert
// "no dead state is present".
const _noDead: AnyDead extends false ? true : false = true;

// Per-state checks (defense-in-depth: even if `IsExact` is bypassed
// by a clever refactor, these per-literal asserts still catch the
// common regression of removing one specific state).
const _hasIdle: "idle" extends RecordingState ? true : false = true;
const _hasRecording: "recording" extends RecordingState ? true : false = true;
const _hasTranscribing: "transcribing" extends RecordingState ? true : false =
	true;
const _hasLoading: "loading" extends RecordingState ? true : false = true;
const _hasCancelling: "cancelling" extends RecordingState ? true : false = true;
const _hasError: "error" extends RecordingState ? true : false = true;

describe("RecordingState union, RW-0 rewrite of test_only_six_states", () => {
	it("contains exactly the 6 backend-emitted states (idle, recording, transcribing, loading, cancelling, error)", () => {
		// The compile-time `_isExact` const above guarantees
		// the union is exactly the 6-state set.  The runtime
		// assertion is a tautology but ensures the test
		// actually runs and shows up in CI reports.
		expect(_isExact).toBe(true);
	});

	it("includes each of the 6 expected states individually", () => {
		// Defense-in-depth: a refactor that uses `string`
		// (which would make IsExact fail) is caught here too.
		expect(_hasIdle).toBe(true);
		expect(_hasRecording).toBe(true);
		expect(_hasTranscribing).toBe(true);
		expect(_hasLoading).toBe(true);
		expect(_hasCancelling).toBe(true);
		expect(_hasError).toBe(true);
	});
});

describe("RecordingState union, RW-0 rewrite of test_dead_states_removed", () => {
	it("does NOT contain any of the 7 dead values (listening, processing, warming_up, downloading, paused, setup, not_configured)", () => {
		// The compile-time `_noDead` const above guarantees
		// none of the dead-state literals are members of
		// RecordingState.  If someone re-adds one, the file
		// fails to compile.
		expect(_noDead).toBe(true);
	});
});
