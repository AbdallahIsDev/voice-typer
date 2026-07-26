// @vitest-environment node
/**
 * DE-87 unit tests: the 5 listed Electron main-process modules use
 * the structured `log` logger (ESM import from ../logging) instead of
 * raw console.* calls, so log messages persist in packaged builds.
 *
 * Source-level tests — no module imports required (avoids Electron mock
 * conflicts with other test files).
 */
import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const modules = [
        { name: "python/handle-message.ts", path: "../python/handle-message.ts" },
        { name: "python/start-python.ts", path: "../python/start-python.ts" },
        { name: "python/tcp-connect.ts", path: "../python/tcp-connect.ts" },
        { name: "index.ts", path: "../index.ts" },
        { name: "ipc/bubble-handlers.ts", path: "../ipc/bubble-handlers.ts" },
];

describe("DE-87: 5 modules use structured log.* (not raw console.*)", () => {
        for (const mod of modules) {
                it(`${mod.name} imports log from ../logging (ESM)`, () => {
                        const src = fs.readFileSync(path.resolve(__dirname, mod.path), "utf-8");
                        // ESM import of `log` from a logging module.
                        expect(src).toMatch(
                                /import\s+\{[^}]*\blog\b[^}]*\}\s+from\s+["'][^"']*logging["']/,
                        );
                });

                it(`${mod.name} has no raw console.warn/console.error/console.log calls (outside comments)`, () => {
                        const src = fs.readFileSync(path.resolve(__dirname, mod.path), "utf-8");
                        // Strip /* ... */ block comments and // ... line
                        // comments, then check for console.* calls.
                        const stripped = src
                                .replace(/\/\*[\s\S]*?\*\//g, "")
                                .replace(/\/\/.*$/gm, "");
                        const consoleCalls = stripped.match(/console\.(warn|error|log)\s*\(/g);
                        expect(consoleCalls).toBeNull();
                });
        }

        it("all 5 modules use the same resolution pattern (ESM import, not require())", () => {
                for (const mod of modules) {
                        const src = fs.readFileSync(path.resolve(__dirname, mod.path), "utf-8");
                        // Strip /* ... */ block comments and // ... line
                        // comments before checking, so a `require("../logging")`
                        // reference inside a comment (e.g. bubble-handlers.ts:21)
                        // does not trigger a false positive. The check is meant to
                        // catch actual runtime require() calls only.
                        const stripped = src
                                .replace(/\/\*[\s\S]*?\*\//g, "")
                                .replace(/\/\/.*$/gm, "");
                        // The logging import must be ESM (no require()).
                        const requireMatches = stripped.match(
                                /require\(\s*["'][^"']*logging["']\s*\)/g,
                        );
                        expect(requireMatches).toBeNull();
                }
        });
});
