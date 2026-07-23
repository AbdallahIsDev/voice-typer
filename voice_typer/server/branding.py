"""Centralized branding constants.

BRAND-001: single source of truth for the application name.
Change APP_NAME here and it propagates everywhere.

The renderer mirrors these constants in
``voice_typer/client/src/renderer/src/branding.ts`` — keep the two
files in sync when renaming the product.

╔══════════════════════════════════════════════════════════════════════╗
║  ⚠️  AI / AGENT WARNING — DO NOT REVERT OR HARDCODE                    ║
║                                                                       ║
║  Do NOT replace APP_NAME usages with the hardcoded string             ║
║  "Voice Typer" (or "VoiceTyper") elsewhere in the codebase.           ║
║  Even though APP_NAME currently resolves to "Voice Typer",             ║
║  the VARIABLE exists so the app name can be changed in ONE            ║
║  place (this file) and propagate everywhere automatically.            ║
║                                                                       ║
║  Reverting to a hardcoded string defeats the entire purpose           ║
║  of this file and creates a maintenance nightmare when the             ║
║  product is renamed.                                                  ║
║                                                                       ║
║  If you are an AI agent and feel tempted to inline the value —        ║
║  DON'T. Use `from voice_typer.server.branding import APP_NAME`.       ║
║                                                                       ║
║  This is enforced by `scripts/check_branding.py` in CI.               ║
║  Violations will fail the build.                                      ║
╚══════════════════════════════════════════════════════════════════════╝
"""

APP_NAME = "Voice Typer"
APP_DESCRIPTION = "Background voice-to-text utility"
APP_URL = "https://github.com/AbdallahIsDev/voice-typer"
APP_REPO = "AbdallahIsDev/voice-typer"
