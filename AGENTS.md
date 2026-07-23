# Project-level agent instructions for voice-typer

## Branding — DO NOT HARDCODE APP NAME

**CRITICAL RULE:** Never replace `APP_NAME` usages with the hardcoded string
"Voice Typer" (or "VoiceTyper") anywhere in the codebase. Even though
`APP_NAME` currently resolves to "Voice Typer", the VARIABLE exists so the
app name can be changed in ONE place and propagate everywhere automatically.

If you are an AI agent and feel tempted to inline the value — **DON'T**.

- **Python:** `from voice_typer.server.branding import APP_NAME`
- **TypeScript (main):** `import { APP_NAME } from './branding'`
- **TypeScript (renderer):** `import { APP_NAME } from '../branding'`

This is enforced by `scripts/check_branding.py` in CI. Violations will fail
the build. The check is NOT optional and must NOT be disabled or bypassed.

Source of truth files (only these may contain the literal string):
1. `voice_typer/server/branding.py`
2. `voice_typer/client/src/main/branding.ts`
3. `voice_typer/client/src/renderer/src/branding.ts`

## Pinned Action Versions — DO NOT DOWNGRADE

All GitHub Actions are pinned to Node 24-compatible versions (see the comment
block at the top of `.github/workflows/build.yml`). Node 20 was deprecated
2025-09-19. Do not downgrade action versions (e.g. `actions/checkout@v5` back
to `@v4`) — it reintroduces Node 20 runtime deprecation warnings and the
`[DEP0040] punycode` DeprecationWarning.

## npm Overrides — DO NOT REMOVE

`voice_typer/client/package.json` contains `overrides` that force-upgrade
deprecated transitive deps (`@electron/asar`, `@electron/get`,
`@hono/node-server`). These eliminate deprecation warnings and security
vulnerabilities. Do not remove or downgrade them. See the `//overrides_note`
comment in package.json for full rationale.
