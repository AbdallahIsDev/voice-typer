import { describe, expect, it } from "vitest";

describe("test-setup storage cleanup (afterEach clears both storages)", () => {
	it("step 1, writes filter-state keys into both storages (no local cleanup)", () => {
		sessionStorage.setItem("vt:filters:history.searchQuery", '"hel"');
		sessionStorage.setItem("vt:filters:vocabulary.sortOrder", '"newest"');
		localStorage.setItem("vt_some_local_key", "persisted");

		// Sanity: the writes actually landed (a clean-room bug in the
		// mock Storage would otherwise make step 2 pass vacuously).
		expect(sessionStorage.getItem("vt:filters:history.searchQuery")).toBe(
			'"hel"',
		);
		expect(sessionStorage.getItem("vt:filters:vocabulary.sortOrder")).toBe(
			'"newest"',
		);
		expect(localStorage.getItem("vt_some_local_key")).toBe("persisted");
		// No cleanup() on purpose, the setup file's afterEach owns it.
	});

	it("step 2, sessionStorage AND localStorage are empty again (afterEach cleared them)", () => {
		// sessionStorage must be cleared by the centralised afterEach:
		// a `vt:filters:*` key leaking here is exactly the latent
		// order-dependence the cleanup exists to prevent.
		expect(sessionStorage.getItem("vt:filters:history.searchQuery")).toBeNull();
		expect(
			sessionStorage.getItem("vt:filters:vocabulary.sortOrder"),
		).toBeNull();
		expect(sessionStorage.length).toBe(0);

		// localStorage keeps its long-standing cleanup contract.
		expect(localStorage.getItem("vt_some_local_key")).toBeNull();
		expect(localStorage.length).toBe(0);
	});
});
