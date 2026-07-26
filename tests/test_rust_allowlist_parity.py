"""YJ-10 redundant parity guard: Rust ↔ TS allowlist byte-for-byte equality.

This test file is a SECOND layer of defense for the YJ-10 invariant
(Rust ``allowed_commands()`` set MUST mirror the TS ``ALLOWED_COMMANDS``
Set exactly — same count + same entries). The primary guard lives in
``tests/test_security_doc_command_count.py`` (functions
``test_rust_allowlist_matches_ts_allowlist_count`` and
``test_rust_allowlist_matches_ts_allowlist_entries``). This file
exists so that:

1. **The YJ-10 finding has its own dedicated test file.** Group 5's
   review process assigns each finding a regression test that lives
   close to the finding's documentation.
   ``test_security_doc_command_count.py`` predates YJ-10 (it was
   d-review Finding 5 + CR-4 Fix-C) — its scope is broader (also
   covers SECURITY.md doc-count parity). This file is YJ-10-specific:
   it asserts ONLY the Rust ↔ TS parity invariant, with a clearer
   failure message that points at the YJ-10 fix's two files.

2. **A negative-regression guard for the 17 removed commands.** The
   ``test_rust_allowlist_does_notContain_yj10_removed_commands``
   test below asserts that none of the 17 commands removed by the
   YJ-10 fix silently creep back into the Rust allowlist. Each of
   those 17 was audited via
   ``rg --type=ts '<cmd>' voice_typer/client/src/renderer/src/``
   and confirmed to have ZERO renderer callers; re-adding one would
   re-open the defense-in-depth gap.

The test imports the Rust allowlist by parsing ``sidecar_cmds.rs``
(string scan for the ``let cmds: &[&str] = &[`` ... ``];`` literal
inside ``allowed_commands()``) and compares against the TS
``ALLOWED_COMMANDS`` (string scan for the
``ALLOWED_COMMANDS = new Set([`` ... ``]);`` literal).

If you're modifying the Rust allowlist shape and the parser here
breaks, prefer updating the parser (the regex is intentionally
specific) over skipping the test — the YJ-10 invariant is a
defense-in-depth gate against a compromised renderer reaching
server-side handlers the renderer never legitimately invokes.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SIDECAR_CMDS_RS = REPO_ROOT / "src-tauri" / "src" / "commands" / "sidecar_cmds.rs"
ALLOWED_COMMANDS_TS = REPO_ROOT / "voice_typer" / "client" / "src" / "main" / "allowed-commands.ts"


def _rust_allowed_commands() -> set[str]:
    """Parse the Rust ``allowed_commands()`` body for quoted command names.

    YJ-10: the Rust source stores the allowlist as a ``&[&str]`` slice
    literal inside the ``allowed_commands()`` function's
    ``ALLOWED_COMMANDS.get_or_init`` closure. We anchor on
    ``let cmds: &[&str] = &[`` (the start of the literal) and extract
    each ``"<snake_case_name>"`` token until the matching ``];``.

    This mirrors the primary parser in
    ``test_security_doc_command_count.py::_allowed_commands_rust``
    rather than using a more permissive regex (which would also match
    the error-envelope field names like ``"type"``, ``"code"``,
    ``"data"``, ``"message"``, ``"disallowed_window"`` that appear in
    ``require_main_window`` above the literal). The duplication is
    intentional — if the literal shape changes, both parsers fail
    loudly with the same actionable error message.
    """
    src = SIDECAR_CMDS_RS.read_text(encoding="utf-8")
    m_start = re.search(r"let\s+cmds:\s*&\[&str\]\s*=\s*&\[", src)
    assert m_start is not None, (
        "src-tauri/src/commands/sidecar_cmds.rs no longer declares the "
        "`let cmds: &[&str] = &[` literal inside `allowed_commands()`. "
        "Did the constructor shape change? Update this parser (and the "
        "primary parser in test_security_doc_command_count.py) to match."
    )
    body = src[m_start.end() :]
    m_end = re.search(r"\];", body)
    assert m_end is not None, (
        "src-tauri/src/commands/sidecar_cmds.rs: could not find the "
        "closing `];` of the `let cmds: &[&str] = &[` literal. Update "
        "this parser if the literal shape changed."
    )
    literal = body[: m_end.start()]
    return set(re.findall(r'"([a-z_]+)"', literal))


def _ts_allowed_commands() -> set[str]:
    """Parse the TS ``ALLOWED_COMMANDS = new Set([...])`` literal.

    Mirrors the parser in
    ``test_security_doc_command_count.py::_allowed_commands_ts`` —
    same regex, same anchoring. Duplicated here so this test file is
    self-contained (no cross-file import of a private helper).
    """
    src = ALLOWED_COMMANDS_TS.read_text(encoding="utf-8")
    start = src.index("ALLOWED_COMMANDS = new Set([")
    end = src.index("]);", start)
    block = src[start:end]
    return set(re.findall(r'"([a-z_]+)"', block))


def test_rust_allowlist_count_matches_ts() -> None:
    """YJ-10: Rust allowlist count MUST equal TS allowlist count.

    A count mismatch means a command was added to one file but not
    the other — the entry-level test below pinpoints which one.
    """
    rust = _rust_allowed_commands()
    ts = _ts_allowed_commands()
    assert len(rust) == len(ts), (
        f"YJ-10 parity broken: Rust ALLOWED_COMMANDS has {len(rust)} "
        f"entries but TS has {len(ts)}. Update both files in the same "
        f"PR. Files:\n"
        f"  - Rust: src-tauri/src/commands/sidecar_cmds.rs "
        f"(allowed_commands fn)\n"
        f"  - TS:   voice_typer/client/src/main/allowed-commands.ts "
        f"(ALLOWED_COMMANDS = new Set([...]))"
    )


def test_rust_allowlist_entries_match_ts() -> None:
    """YJ-10: Rust allowlist entries MUST equal TS allowlist entries.

    Catches the case where the counts match but the entries differ
    (e.g. a typo renamed ``quit_app`` to ``quit`` in one file but not
    the other). Reports the symmetric difference so the contributor
    sees exactly which commands are in only one of the two files.
    """
    rust = _rust_allowed_commands()
    ts = _ts_allowed_commands()
    only_rust = rust - ts
    only_ts = ts - rust
    assert not only_rust and not only_ts, (
        f"YJ-10 entry-level drift detected:\n"
        f"  In Rust but NOT in TS: {sorted(only_rust) or '(none)'}\n"
        f"  In TS but NOT in Rust: {sorted(only_ts) or '(none)'}\n"
        f"Both files MUST list the same commands. Update them in the "
        f"same PR. Files:\n"
        f"  - Rust: src-tauri/src/commands/sidecar_cmds.rs "
        f"(allowed_commands fn)\n"
        f"  - TS:   voice_typer/client/src/main/allowed-commands.ts "
        f"(ALLOWED_COMMANDS = new Set([...]))\n"
        f"If a command was intentionally removed from one file, "
        f"remove it from the other too. If a command was intentionally "
        f"added to one file, add it to the other too. See the YJ-10 "
        f"reconciliation note in sidecar_cmds.rs::allowed_commands "
        f"for the audit criteria (renderer-caller check)."
    )


def test_rust_allowlist_does_not_contain_yj10_removed_commands() -> None:
    """YJ-10 negative regression guard.

    The 17 commands removed by the YJ-10 fix (none had renderer
    callers — see the reconciliation note in
    ``sidecar_cmds.rs::allowed_commands``) MUST NOT silently creep
    back into the Rust allowlist. Each of these 17 was audited via
    ``rg --type=ts '<cmd>' voice_typer/client/src/renderer/src/`` and
    confirmed to have ZERO renderer callers; re-adding one would
    re-open the defense-in-depth gap (a compromised renderer would
    be able to ``invoke('dispatch', {cmd:'<one of these 17>'})`` and
    reach a server-side handler that no legitimate UI path
    exercises).

    If a future contributor legitimately adds a renderer caller for
    one of these commands (e.g. wires up a Settings page button for
    ``export_diagnostics``), they MUST:
      1. Add the command back to BOTH the Rust allowlist AND the TS
         allowlist (in the same PR).
      2. Remove the command name from the ``yj10_removed`` set below
         so this negative-regression guard no longer flags it.
    """
    rust = _rust_allowed_commands()
    yj10_removed = {
        "apply_vocabulary_suggestion",
        "check_accessibility",
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
        "show_electron_notification",
        "test_llm_connection",
    }
    leaked = yj10_removed & rust
    assert not leaked, (
        f"YJ-10 negative-regression guard: {sorted(leaked)} re-appeared "
        f"in the Rust ALLOWED_COMMANDS. These 17 commands were "
        f"deliberately removed in the YJ-10 fix because they had ZERO "
        f"renderer callers (audit: "
        f"`rg --type=ts '<cmd>' voice_typer/client/src/renderer/src/`). "
        f"Re-adding one re-opens a defense-in-depth gap. If you've "
        f"added a legitimate renderer caller for one of these, ALSO "
        f"add the command to the TS allowlist AND remove it from the "
        f"yj10_removed set in this test (so the guard no longer flags "
        f"the intentional re-addition)."
    )
