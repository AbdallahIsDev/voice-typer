/**
 * One-command Tauri dev environment — the Tauri equivalent of
 * `npm run dev` (Electron).
 *
 * Usage (from voice_typer/client):
 *
 *     npm run tauri:dev
 *
 * What it does, in order:
 *   1. Spawns the Vite dev server (vite.tauri.config.ts — port 1420,
 *      strictPort, HMR, renderer plugins/aliases/CSP) as a child.
 *   2. Waits until http://localhost:1420 answers.
 *   2b. Ensures the Tauri externalBin/resource STUB binaries exist
 *      (`gen_tauri_icons_stub.py --check || generate`). The repo's test
 *      suite deliberately deletes stubs (`--clean` — they are
 *      gitignored build scratch), so without this step every full
 *      pytest run breaks the next `tauri dev` with
 *      "resource path ... doesn't exist". Stubs are regenerated on
 *      demand; real built binaries are never touched.
 *   3. Spawns `tauri dev --config src-tauri/tauri.dev.conf.json` from
 *      the REPO ROOT. The committed override blanks
 *      `build.beforeDevCommand` (the CLI spawns it with a CWD where
 *      the stock `cd voice_typer/client && ...` cannot resolve —
 *      reproduced 2026-08-30; the stock command stays pinned in
 *      tauri.conf.json for CI builds, which DO resolve it). The
 *      Rust host is a debug build, so `dev_mode::is_dev_mode()`
 *      defaults it to the SOURCE Python sidecar — no env vars needed.
 *   4. Forwards everything; Ctrl+C (or either child exiting) tears the
 *      whole tree down (taskkill /T — Node's child.kill does not kill
 *      Windows process trees).
 *
 * Rust file changes: the tauri CLI rebuilds + relaunches the app
 * automatically. Renderer file changes: Vite HMR pushes instantly.
 */
import { spawn, spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const clientDir = path.resolve(__dirname, "..");
const repoRoot = path.resolve(clientDir, "..", "..");
const devOverride = path.join(repoRoot, "src-tauri", "tauri.dev.conf.json");

// NOTE: `localhost`, not 127.0.0.1 — Vite binds whichever stack
// `localhost` resolves to (::1 on this machine) and the tauri CLI's
// own devUrl wait also uses `localhost`; polling 127.0.0.1 hangs.
const VITE_URL = "http://localhost:1420/";
const VITE_WAIT_TIMEOUT_MS = 60_000;

/** Kill a process tree on Windows (child.kill() does not traverse). */
function killTree(pid) {
	if (!pid) return;
	spawn("taskkill", ["/T", "/F", "/PID", String(pid)], {
		stdio: "ignore",
		windowsHide: true,
	});
}

let viteChild = null;
let cliChild = null;
let shuttingDown = false;

function teardown(exitCode) {
	if (shuttingDown) return;
	shuttingDown = true;
	killTree(viteChild?.pid);
	killTree(cliChild?.pid);
	process.exitCode = exitCode ?? 0;
	// give taskkill a beat to land before the process exits
	setTimeout(() => process.exit(process.exitCode ?? 0), 300);
}

process.on("SIGINT", () => teardown(0));
process.on("SIGTERM", () => teardown(0));
process.on("exit", () => {
	// last-resort (SIGKILL on us): still try to reap children
	killTree(viteChild?.pid);
	killTree(cliChild?.pid);
});

async function waitForVite() {
	const deadline = Date.now() + VITE_WAIT_TIMEOUT_MS;
	while (Date.now() < deadline) {
		try {
			const res = await fetch(VITE_URL, { signal: AbortSignal.timeout(1500) });
			if (res.ok) return;
		} catch {
			/* not up yet */
		}
		await new Promise((r) => setTimeout(r, 400));
	}
	throw new Error(
		`Vite dev server did not answer on ${VITE_URL} within ${VITE_WAIT_TIMEOUT_MS / 1000}s`,
	);
}

// ── 1. Vite dev server (HMR) ─────────────────────────────────────────
console.log("[tauri-dev] starting Vite (http://localhost:1420)...");
viteChild = spawn("cmd", ["/c", "npx", "vite", "--config", "vite.tauri.config.ts"], {
	cwd: clientDir,
	stdio: ["ignore", "inherit", "inherit"],
	windowsHide: true,
});
viteChild.on("exit", (code) => {
	if (!shuttingDown) {
		console.error(`[tauri-dev] vite exited early (code=${code}) — aborting`);
		teardown(1);
	}
});

try {
	await waitForVite();
} catch (e) {
	console.error(`[tauri-dev] ${e.message}`);
	teardown(1);
	// teardown schedules process.exit — never fall through to the CLI.
	await new Promise(() => {});
}

// ── 2b. Ensure the Tauri stub binaries exist ─────────────────────────
//
// The stub generator is the sanctioned flow (repo AGENTS.md dev notes):
// `--check` exits 0 iff every externalBin/resource path is present AND
// structurally valid (stub bytes or a REAL binary); the bare run
// generates what is missing and never touches real artifacts. The test
// suite's `--clean` deletes stubs by design, so this check-then-generate
// must run before EVERY `tauri dev` — otherwise pytest (often running
// concurrently in another terminal) breaks the next dev launch.
console.log("[tauri-dev] checking Tauri stub binaries...");
const stubScript = path.join(repoRoot, "scripts", "gen_tauri_icons_stub.py");
const stubCheck = spawnSync("python", [stubScript, "--check"], {
	cwd: repoRoot,
	windowsHide: true,
});
if (stubCheck.status !== 0) {
	console.log("[tauri-dev] stubs missing — regenerating...");
	const gen = spawnSync("python", [stubScript], {
		cwd: repoRoot,
		stdio: "inherit",
		windowsHide: true,
	});
	if (gen.status !== 0) {
		console.error(
			"[tauri-dev] stub generation failed — aborting (see output above)",
		);
		teardown(1);
		await new Promise(() => {});
	}
}

// ── 3. Tauri CLI (Rust host + sidecar supervisor) ────────────────────
console.log("[tauri-dev] starting tauri dev (debug host + source sidecar)...");
cliChild = spawn(
	"cmd",
	[
		"/c",
		"npx",
		"@tauri-apps/cli",
		"dev",
		"--config",
		devOverride,
	],
	{
		cwd: repoRoot,
		stdio: ["inherit", "inherit", "inherit"],
		windowsHide: true,
	},
);
cliChild.on("exit", (code) => {
	// The CLI owns the Rust host; when it exits (app closed / Ctrl+C in
	// the CLI's console), the whole dev session is done.
	if (!shuttingDown) teardown(code ?? 0);
});

// Keep the orchestrator alive while the CLI runs.
await new Promise(() => {});
