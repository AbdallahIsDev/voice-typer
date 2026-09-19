"""YJ-10 negative-regression guard for the Rust allowlist."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SIDECAR_CMDS_RS = REPO_ROOT / "src-tauri" / "src" / "commands" / "sidecar_cmds.rs"
SIDECAR_CMDS_DIR = REPO_ROOT / "src-tauri" / "src" / "commands" / "sidecar_cmds"


def _read_sidecar_cmds_module() -> str:
    """Concatenate sidecar_cmds.rs + sidecar_cmds/*.rs (EO-35 split)."""
    files = [SIDECAR_CMDS_RS] + sorted(SIDECAR_CMDS_DIR.glob("*.rs"))
    return "\n\n".join(p.read_text(encoding="utf-8") for p in files)


def _rust_allowed_commands() -> set[str]:
    """Parse the Rust ``allowed_commands()`` body for quoted command names."""
    src = _read_sidecar_cmds_module()
    m_start = re.search(r"let\s+cmds:\s*&\[&str\]\s*=\s*&\[", src)
    assert m_start is not None, (
        "src-tauri/src/commands/sidecar_cmds/allowlist.rs no longer "
        "declares the `let cmds: &[&str] = &[` literal inside "
        "`allowed_commands()`. Update this parser (and the primary "
        "parser in test_security_doc_command_count.py) to match."
    )
    body = src[m_start.end() :]
    m_end = re.search(r"\];", body)
    assert m_end is not None, (
        "src-tauri/src/commands/sidecar_cmds/allowlist.rs: could not "
        "find the closing `];` of the `let cmds: &[&str] = &[` literal."
    )
    literal = body[: m_end.start()]
    return set(re.findall(r'"([a-z_]+)"', literal))


def test_rust_allowlist_does_not_contain_removed_commands() -> None:
    """
    YJ-10 negative regression guard.
    ``sidecar_cmds.rs::allowed_commands``) MUST NOT silently creep
    """
    rust = _rust_allowed_commands()
    yj10_removed = {
        "apply_vocabulary_suggestion",
        "delete_all_personal_data",
        "dismiss_vocabulary_suggestion",
        "export_diagnostics",
        "export_gdpr_bundle",
        "get_audio_status",
        "get_rms_level",
        "get_vocabulary_suggestions",
        "level_monitor_status",
        "microphone_test_status",
        "onboarding_get_model_catalog",
        "onboarding_get_step",
        "onboarding_request_keyboard_permission",
        "refresh_microphones",
        "test_llm_connection",
    }
    leaked = yj10_removed & rust
    assert not leaked, (
        f"YJ-10 negative-regression guard: {sorted(leaked)} re-appeared "
        f"in the Rust ALLOWED_COMMANDS. These 16 commands were "
        f"deliberately removed in the YJ-10 fix because they had ZERO "
        f"renderer callers (audit: "
        f"`rg --type=ts '<cmd>' voice_typer/client/src/renderer/src/`). "
        f"Re-adding one re-opens a defense-in-depth gap. If you've "
        f"added a legitimate renderer caller for one of these, ALSO "
        f"remove it from the yj10_removed set in this test (so the "
        f"guard no longer flags the intentional re-addition)."
    )
