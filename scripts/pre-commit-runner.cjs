/**
 * Cross-platform pre-commit hook runner.
 * Usage: node scripts/pre-commit-runner.cjs <hook-id>
 *
 * Resolves paths relative to the repo root and works on both
 * POSIX and Windows without requiring bash in PATH.
 */
const { execSync } = require("child_process");
const path = require("path");

const repoRoot = path.resolve(__dirname, "..");
const clientDir = path.join(repoRoot, "voice_typer", "client");

const COMMANDS = {
  "biome-check": {
    cmd: "npx biome check",
    cwd: clientDir,
  },
  "client-typecheck": {
    cmd: "npm run typecheck",
    cwd: clientDir,
  },
  "hotkey-reserved-sync": {
    cmd: "python -m pytest tests/test_hotkey_reserved_sync.py -q --no-cov",
    cwd: repoRoot,
  },
};

const hookId = process.argv[2];
const config = COMMANDS[hookId];

if (!config) {
  console.error(`Unknown hook: ${hookId}`);
  process.exit(1);
}

try {
  execSync(config.cmd, {
    cwd: config.cwd,
    stdio: "inherit",
    shell: true,
  });
} catch {
  process.exit(1);
}
