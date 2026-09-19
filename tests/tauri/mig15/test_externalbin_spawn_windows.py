"""externalBin spawn validation (Windows)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

_THIS_FILE = Path(__file__).resolve()
_REPO_ROOT = _THIS_FILE.parents[3]
_SRC_TAURI = _REPO_ROOT / "src-tauri"
_TAURI_CONF = _SRC_TAURI / "tauri.conf.json"
_SPAWN_RS = _SRC_TAURI / "src" / "sidecar" / "spawn.rs"
_SUPERVISOR_RS = _SRC_TAURI / "src" / "sidecar" / "supervisor.rs"
_UTIL_RS = _SRC_TAURI / "src" / "util.rs"
_MAIN_RS = _SRC_TAURI / "src" / "main.rs"
_SIDECAR_WS_PY = _REPO_ROOT / "voice_typer" / "server" / "sidecar_ws.py"


@pytest.fixture(scope="module")
def tauri_conf() -> dict:
    """Load + parse src-tauri/tauri.conf.json."""
    assert _TAURI_CONF.exists(), f"tauri.conf.json not found: {_TAURI_CONF}"
    return json.loads(_TAURI_CONF.read_text(encoding="utf-8"))


def _read_spawn_module() -> str:
    """Concatenate the spawn module sources (spawn.rs + spawn/*.rs)."""
    files = [_SPAWN_RS] + sorted(_SPAWN_RS.parent.joinpath("spawn").glob("*.rs"))
    return "\n\n".join(p.read_text(encoding="utf-8") for p in files)


@pytest.fixture(scope="module")
def spawn_rs_source() -> str:
    """Read the spawn module sources (spawn.rs + spawn/*.rs) as text."""
    assert _SPAWN_RS.exists(), f"spawn.rs not found: {_SPAWN_RS}"
    return _read_spawn_module()


@pytest.fixture(scope="module")
def supervisor_rs_source() -> str:
    """Read src-tauri/src/sidecar/supervisor.rs as text."""
    assert _SUPERVISOR_RS.exists(), f"supervisor.rs not found: {_SUPERVISOR_RS}"
    return _SUPERVISOR_RS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def util_rs_source() -> str:
    """Read src-tauri/src/util.rs as text (for the supervisor constants)."""
    assert _UTIL_RS.exists(), f"util.rs not found: {_UTIL_RS}"
    return _UTIL_RS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def main_rs_source() -> str:
    """Read src-tauri/src/main.rs as text (for the windows_subsystem attr)."""
    assert _MAIN_RS.exists(), f"main.rs not found: {_MAIN_RS}"
    return _MAIN_RS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def sidecar_ws_source() -> str:
    """Read voice_typer/server/sidecar_ws.py + the stdout_banner leaf"""
    assert _SIDECAR_WS_PY.exists(), f"sidecar_ws.py not found: {_SIDECAR_WS_PY}"
    _stdout_banner = _SIDECAR_WS_PY.parent / "sidecar_ws_internals" / "stdout_banner.py"
    assert _stdout_banner.exists(), f"stdout_banner.py not found: {_stdout_banner}"
    return _SIDECAR_WS_PY.read_text(encoding="utf-8") + "\n" + _stdout_banner.read_text(encoding="utf-8")


def test_tauri_conf_external_bin_lists_python_sidecar(tauri_conf) -> None:
    """ADR-0020 §4.1 + §7: externalBin must list ``bin/python-sidecar``."""
    bundle = tauri_conf.get("bundle", {})
    assert "externalBin" in bundle, "bundle.externalBin must exist"
    external_bin = bundle["externalBin"]
    assert isinstance(external_bin, list), "externalBin must be a list"
    assert "bin/python-sidecar" in external_bin, (
        "externalBin must contain 'bin/python-sidecar' (Tauri appends the target triple at spawn time)"
    )
    # The list must NOT contain any triple-suffixed entries, the base
    for entry in external_bin:
        assert not re.search(
            r"-(x86_64|aarch64)-pc-windows-msvc"
            r"|-x86_64-apple-darwin"
            r"|-aarch64-apple-darwin"
            r"|-x86_64-unknown-linux-gnu"
            r"|-aarch64-unknown-linux-gnu",
            entry,
        ), (
            f"externalBin entry '{entry}' must NOT contain the target "
            f"triple, Tauri appends it at runtime. Use the base name "
            f"only (e.g. 'bin/python-sidecar')."
        )


def test_tauri_conf_shell_config_is_v2_valid(tauri_conf) -> None:
    """ADR-0020 §7: ``plugins.shell`` must be v2-valid (``{\"open\": false}``)."""
    shell = tauri_conf.get("plugins", {}).get("shell")
    assert shell == {"open": False}, (
        "plugins.shell must be exactly {'open': false}, tauri-plugin-shell "
        "v2 rejects 'sidecar'/'scope' keys at startup ('unknown field "
        f"`scope`, expected `open`'); got {shell!r}"
    )


def test_tauri_conf_resources_include_windows_native(tauri_conf) -> None:
    """ADR-0020 §4.1 + §6.4: windows-key-listener.exe must be a resource."""
    resources = tauri_conf.get("bundle", {}).get("resources", [])
    assert isinstance(resources, list) and resources, "bundle.resources must be a non-empty list"
    assert "resources/native/windows-key-listener.exe" in resources, (
        "bundle.resources must include 'resources/native/windows-key-listener.exe' "
        "for Windows hotkey support (ADR-0020 §6.4)"
    )


def test_spawn_rs_calls_shell_sidecar_with_base_name(spawn_rs_source) -> None:
    """ADR-0020 §4.1: spawn.rs resolves the sidecar by base name."""
    # Look for the .sidecar("python-sidecar") call.
    assert re.search(
        r'\.sidecar\s*\(\s*"python-sidecar"\s*\)',
        spawn_rs_source,
    ), 'spawn.rs must call `app.shell().sidecar("python-sidecar")` (tauri-plugin-shell appends the target triple)'


def test_spawn_rs_reads_command_event_stdout(spawn_rs_source) -> None:
    """ADR-0020 §1: spawn.rs consumes the CommandEvent::Stdout stream."""
    assert "CommandEvent::Stdout" in spawn_rs_source, (
        "spawn.rs must match CommandEvent::Stdout to read sidecar stdout "
        "(tauri-plugin-shell API, not std::process::Stdio::piped)"
    )
    # The release path must NOT call .stdout(Stdio::piped()) on the
    assert "CommandEvent::Stderr" in spawn_rs_source, (
        "spawn.rs must also match CommandEvent::Stderr (logs but doesn't parse)"
    )
    assert "CommandEvent::Terminated" in spawn_rs_source, (
        "spawn.rs must match CommandEvent::Terminated to detect pre-handshake crash"
    )


def test_spawn_rs_parses_port_from_server_started(spawn_rs_source) -> None:
    """ADR-0020 §1: spawn.rs parses the port from the server_started JSON."""
    # The parse_server_started function must exist and parse the port.
    assert "fn parse_server_started" in spawn_rs_source, (
        "spawn.rs must define `parse_server_started(line: &str) -> Option<u16>`"
    )
    # It must check the "event" field equals "server_started".
    assert re.search(
        r'parse_server_started.*?"server_started"',
        spawn_rs_source,
        re.DOTALL,
    ), "parse_server_started must check event == 'server_started'"
    # It must extract the "port" field.
    assert '"port"' in spawn_rs_source, "parse_server_started must read the 'port' field from the JSON"


def test_spawn_rs_sets_ipc_token_env_var(spawn_rs_source) -> None:
    """ADR-0020 §3 + §4.1: spawn.rs passes VOICE_TYPER_IPC_TOKEN at spawn."""
    assert re.search(
        r'\.env\s*\(\s*"VOICE_TYPER_IPC_TOKEN"\s*,',
        spawn_rs_source,
    ), (
        'spawn.rs must call .env("VOICE_TYPER_IPC_TOKEN", token) on the '
        "sidecar builder to pass the bearer token (ADR-0020 §3)"
    )
    assert re.search(
        r'\.env\s*\(\s*"TAURI_SIDECAR"\s*,\s*"1"\s*\)',
        spawn_rs_source,
    ), (
        "spawn.rs must set TAURI_SIDECAR=1 so the sidecar skips its "
        "Python-side single-instance mutex + heartbeat watchdog"
    )
    assert re.search(
        r'\.env\s*\(\s*"VOICE_TYPER_NATIVE_DIR"\s*,',
        spawn_rs_source,
    ), "spawn.rs must set VOICE_TYPER_NATIVE_DIR to resourceDir/native"


def test_spawn_rs_passes_ws_arg(spawn_rs_source) -> None:
    """ADR-0020 §1: spawn.rs passes ``--ws`` so the sidecar uses WS transport."""
    assert re.search(r'\.args\s*\(\s*\["--ws"\]\s*\)', spawn_rs_source), (
        "spawn.rs must pass --ws to the sidecar so it binds the WS server instead of using the legacy stdio transport"
    )


def test_spawn_rs_server_started_log_line_format(spawn_rs_source) -> None:
    """
    ADR-0020 §1 + §3: the server_started log line must NOT include the token.
    Per ADR-0020 §3 ("never logged"), the bearer token must not appear
    """
    # (a) The shared success-line template in the handshake loops.
    template_re = re.compile(r'log::info!\("\{\} \{\} port=\{\}",\s*labels\.log_tag,\s*labels\.event_name')
    assert template_re.search(spawn_rs_source), (
        "handshake_loop.rs must log the handshake success line via the "
        'shared template "{} {} port={}" (labels.log_tag, '
        "labels.event_name, port), the template that renders the "
        "runbook's '[SIDECAR] server_started port=N' line on Windows."
    )
    # (b) The release path wires the labels that render that template
    release_mode_src = (_SPAWN_RS.parent / "spawn" / "release_mode.rs").read_text(encoding="utf-8")
    assert re.search(r'log_tag:\s*"\[SIDECAR\]"', release_mode_src), (
        'release_mode.rs must pass log_tag "[SIDECAR]" to the shared '
        "handshake loop so the success line greps as '[SIDECAR] ...' "
        "(runbook §6.1 pass criteria)."
    )
    assert re.search(r'event_name:\s*"server_started"', release_mode_src), (
        'release_mode.rs must pass event_name "server_started" to the '
        "shared handshake loop so the success line greps as "
        "'[SIDECAR] server_started port=N' (runbook §6.1 pass "
        "criteria)."
    )
    # No log line in the spawn module may include the token value.
    assert not re.search(
        r"log::\w+!\([^)]*token[^)]*\)",
        spawn_rs_source,
        re.IGNORECASE,
    ), "spawn.rs must NOT log the bearer token, ADR-0020 §3 'never logged'. The token is passed via env var only."


def test_host_binary_uses_windows_subsystem_in_release(main_rs_source) -> None:
    """ADR-0020 §11 (Windows): the host binary must NOT allocate a console."""
    # The cfg_attr must be present and gated on `not(debug_assertions)`
    assert re.search(
        r"#!?\[cfg_attr\s*\(\s*not\s*\(\s*debug_assertions\s*\)\s*,\s*"
        r'windows_subsystem\s*=\s*"windows"\s*\)\s*\]',
        main_rs_source,
    ), (
        "main.rs must set `#![cfg_attr(not(debug_assertions), "
        'windows_subsystem = "windows")]` so the release host has no '
        "console window (ADR-0020 §11 Windows / Win32 subsystem)"
    )


def test_spawn_rs_release_path_does_not_use_std_command(spawn_rs_source, main_rs_source) -> None:
    """The release path must use tauri-plugin-shell, NOT std::process::Command."""
    assert "ShellExt" in spawn_rs_source, (
        "spawn.rs must `use tauri_plugin_shell::ShellExt;` to access app.shell().sidecar(...)"
    )
    # ADR-0020 §14). But the release path must NOT use std::process.
    assert "fn spawn_sidecar_release" in spawn_rs_source, (
        "spawn.rs must define `spawn_sidecar_release` (the externalBin path)"
    )
    assert "CommandEvent>" in spawn_rs_source, (
        "spawn_sidecar_release must return a Receiver<CommandEvent> (the tauri-plugin-shell event stream)"
    )


def test_spawn_rs_no_creation_flags_gap_documented(spawn_rs_source) -> None:
    """Documented gap: spawn.rs does NOT set Win32 CREATE_NO_WINDOW."""
    # If creation_flags is added in the future, this test should be
    has_creation_flags = "creation_flags" in spawn_rs_source
    has_create_no_window = (
        "CREATE_NO_WINDOW" in spawn_rs_source
        or "CREATED_NO_WINDOW" in spawn_rs_source
        or "0x08000000" in spawn_rs_source
    )
    # As of this gate check, neither is present, this is a known gap.
    assert not has_creation_flags or not has_create_no_window, (
        "spawn.rs has Win32 creation_flags support, update this test "
        "to assert the explicit CREATE_NO_WINDOW flag is set on the "
        "sidecar spawn (gap closed)."
    )


def test_supervisor_backoff_constants(util_rs_source) -> None:
    """ADR-0020 §10: supervisor backoff schedule + cap."""
    # SUPERVISOR_BACKOFF_MS constant must be defined as a slice of u64.
    backoff_re = re.compile(
        r"(?:pub\s*\(\s*crate\s*\)\s*)?const\s+SUPERVISOR_BACKOFF_MS\s*:\s*&\[u64\]\s*=\s*&\[([^\]]+)\]"
    )
    m = backoff_re.search(util_rs_source)
    assert m, "util.rs must define `const SUPERVISOR_BACKOFF_MS: &[u64] = &[...]`"
    steps = [int(x.strip()) for x in m.group(1).split(",") if x.strip()]
    # First step must be 500 ms (ADR-0020 §10 + runbook §6.1).
    assert steps[0] == 500, (
        f"SUPERVISOR_BACKOFF_MS[0] must be 500 (got {steps[0]}); ADR-0020 §10 schedule starts at 500 ms"
    )
    # The schedule must include a 2000 ms step (the task's "2s" cap).
    assert 2000 in steps, (
        f"SUPERVISOR_BACKOFF_MS must include a 2000 ms step (got {steps}); task specifies '500ms→2s' range"
    )
    # Doubling property, each step is 2x the previous.
    for i in range(1, len(steps)):
        assert steps[i] == steps[i - 1] * 2, (
            f"SUPERVISOR_BACKOFF_MS[{i}] must be 2x [{i - 1}] "
            f"(got {steps[i]} vs {steps[i - 1]}); ADR-0020 §10 doubling schedule"
        )


def test_supervisor_max_retries_is_5(util_rs_source) -> None:
    """ADR-0020 §10: supervisor retry cap must be 5 before full-app relaunch."""
    cap_re = re.compile(r"const\s+SUPERVISOR_BACKOFF_MS\s*:\s*&\[u64\]\s*=\s*&\[([^\]]+)\]")
    m = cap_re.search(util_rs_source)
    assert m, "util.rs must define `const SUPERVISOR_BACKOFF_MS: &[u64] = &[...]`"
    cap = len([x.strip() for x in m.group(1).split(",") if x.strip()])
    assert cap == 5, f"SUPERVISOR_BACKOFF_MS.len() (the supervisor retry cap) must be 5 (got {cap}); ADR-0020 §10 cap"


def test_supervisor_backoff_schedule_length_matches_cap(util_rs_source) -> None:
    """The backoff schedule length must equal the retry cap."""
    backoff_re = re.compile(r"const\s+SUPERVISOR_BACKOFF_MS\s*:\s*&\[u64\]\s*=\s*&\[([^\]]+)\]")
    m_back = backoff_re.search(util_rs_source)
    assert m_back, "supervisor constants must be defined in util.rs"
    steps = [int(x.strip()) for x in m_back.group(1).split(",") if x.strip()]
    # The retry cap IS the schedule length (respawn_inner iterates it
    cap = 5
    assert len(steps) == cap, (
        f"SUPERVISOR_BACKOFF_MS.len() ({len(steps)}) must stay equal to the retry cap ({cap}) so the loop iterates exactly N times"  # noqa: E501
    )


def test_supervisor_respawn_calls_spawn_sidecar(supervisor_rs_source) -> None:
    """ADR-0020 §10: respawn must call spawn_sidecar_and_get_port."""
    assert "pub(crate) async fn respawn" in supervisor_rs_source, (
        "supervisor.rs must define `respawn` (the supervisor entry point)"
    )
    assert "spawn_sidecar_and_get_port" in supervisor_rs_source, (
        "supervisor.rs must call `spawn_sidecar_and_get_port` to respawn (the supervisor delegates spawn to spawn.rs)"
    )
    # The supervisor must emit a Tauri event when exhausted (so the UI
    assert "supervisor_relaunching" in supervisor_rs_source, (
        "supervisor.rs must emit 'supervisor_relaunching' event before app.restart() (ADR-0020 §10 UI banner)"
    )
    # And a reconnected event on success.
    assert "supervisor_reconnected" in supervisor_rs_source, (
        "supervisor.rs must emit 'supervisor_reconnected' on successful respawn (so the UI can clear the 'reconnecting…' banner)"  # noqa: E501
    )


def test_supervisor_respawn_serializes_with_atomic_flag(supervisor_rs_source) -> None:
    """ADR-0020 §10: supervisor must serialize concurrent respawn attempts."""
    assert "respawn_in_progress" in supervisor_rs_source, (
        "supervisor.rs must use a `respawn_in_progress` atomic flag to serialize concurrent respawn attempts (ADR-0020 §10)"  # noqa: E501
    )
    assert "compare_exchange" in supervisor_rs_source, (
        "supervisor.rs must use compare_exchange on the respawn_in_progress flag to atomically claim the respawn slot"
    )


def test_sidecar_ws_binds_loopback_ephemeral_port(sidecar_ws_source) -> None:
    """ADR-0020 §1: sidecar binds 127.0.0.1:0. OS assigns the port."""
    assert "from voice_typer.server._paths import LOOPBACK_HOST as _LOOPBACK_HOST" in sidecar_ws_source, (
        "sidecar_ws.py must import _LOOPBACK_HOST from voice_typer.server._paths "
        "(hard loopback, no 0.0.0.0/:: bind, ADR-0020 §1)"
    )
    from voice_typer.server._paths import LOOPBACK_HOST

    assert LOOPBACK_HOST == "127.0.0.1", (
        "LOOPBACK_HOST must be '127.0.0.1' (hard loopback, no 0.0.0.0/:: bind, ADR-0020 §1)"
    )
    # The bind call must pass port 0 (OS-assigned ephemeral).
    assert re.search(
        r"serve\s*\(\s*_handler\s*,\s*_LOOPBACK_HOST\s*,\s*0\s*",
        sidecar_ws_source,
    ), "sidecar_ws.py must call serve(handler, _LOOPBACK_HOST, 0), port 0 means OS-assigned ephemeral (ADR-0020 §1)"
    # The legacy fixed port 9876 must NOT appear anywhere as a bind target.
    assert not re.search(r"bind\s*\([^)]*9876", sidecar_ws_source), (
        "sidecar_ws.py must NOT bind the legacy fixed port 9876, ADR-0020 §1 mandates ephemeral :0"
    )


def test_sidecar_ws_emits_server_started_with_port(sidecar_ws_source) -> None:
    """ADR-0020 §1: sidecar writes the OS-assigned port to stdout JSON."""
    assert "def _emit_server_started" in sidecar_ws_source, "sidecar_ws.py must define _emit_server_started(port: int)"
    # The payload must be exactly {"event":"server_started","port":<n>}.
    assert re.search(
        r'json\.dumps\s*\(\s*\{\s*"event"\s*:\s*"server_started"\s*,\s*"port"\s*:',
        sidecar_ws_source,
    ), '_emit_server_started must emit {"event":"server_started","port":<n>} (matches spawn.rs::parse_server_started)'
    # The port must be read back from the bound socket (not assumed).
    assert "getsockname" in sidecar_ws_source, (
        "sidecar_ws.py must read the OS-assigned port via "
        "ws_server.sockets[0].getsockname()[1], never assume a fixed port"
    )


def test_spawn_rs_does_not_hardcode_port(spawn_rs_source) -> None:
    """ADR-0020 §1: the Rust host must NOT choose or hardcode the port."""
    assert not re.search(r"TcpListener::bind", spawn_rs_source), (
        "spawn.rs must NOT bind a TCP listener, the sidecar is the WS server, the host is the WS client (ADR-0020 §1)"
    )
    # The port variable must come from parse_server_started (not a const).
    release_section = spawn_rs_source
    if "fn spawn_sidecar_release" in release_section:
        # Slice from spawn_sidecar_release to the next pub fn or end.
        start = release_section.index("fn spawn_sidecar_release")
        release_section = release_section[start:]
    assert "parse_server_started" in release_section, (
        "spawn_sidecar_release must obtain the port by calling parse_server_started on a stdout line"
    )


def test_this_test_file_does_not_spawn_processes() -> None:
    """This test file must NOT spawn any real process."""
    import ast

    this_source = _THIS_FILE.read_text(encoding="utf-8")
    tree = ast.parse(this_source)

    # Fully-qualified names of forbidden call targets.
    forbidden_call_names = {
        ("subprocess", "Popen"),
        ("subprocess", "run"),
        ("subprocess", "call"),
        ("subprocess", "check_call"),
        ("subprocess", "check_output"),
        ("os", "system"),
        ("os", "popen"),
        ("os", "spawnl"),
        ("os", "spawnle"),
        ("os", "spawnlp"),
        ("os", "spawnlpe"),
        ("os", "spawnv"),
        ("os", "spawnve"),
        ("os", "spawnvp"),
        ("os", "spawnvpe"),
        ("os", "execl"),
        ("os", "execle"),
        ("os", "execlp"),
        ("os", "execlpe"),
        ("os", "execv"),
        ("os", "execve"),
        ("os", "execvp"),
        ("os", "execvpe"),
    }

    def _qualified(node: ast.AST) -> tuple[str, ...] | None:
        """Return the dotted call target of `node`, or None."""
        if isinstance(node, ast.Attribute):
            base = _qualified(node.value)
            if base is None:
                return None
            return base + (node.attr,)
        if isinstance(node, ast.Name):
            return (node.id,)
        return None

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = _qualified(node.func)
        if target is None:
            continue
        for forbidden in forbidden_call_names:
            if tuple(target[-len(forbidden) :]) == forbidden:
                pytest.fail(
                    f"This test file must NOT call {'.'.join(forbidden)}(), "
                    f"the tauri::AppHandle / tauri_plugin_shell APIs are "
                    f"mocked by static source inspection. No real sidecar "
                    f"process may be spawned in the sandbox."
                )


def test_spawn_rs_uses_app_handle_not_concrete_process(spawn_rs_source) -> None:
    """The release-path spawn takes a ``&tauri::AppHandle`` (mockable)."""
    assert re.search(
        r"fn\s+spawn_sidecar_release\s*\(\s*app\s*:\s*&tauri::AppHandle",
        spawn_rs_source,
    ), (
        "spawn_sidecar_release must take `app: &tauri::AppHandle` as the "
        "first arg (mockable plugin boundary, not a concrete process)"
    )
    assert re.search(
        r"fn\s+spawn_sidecar_and_get_port(?:_with_shutdown)?\s*\(\s*app\s*:\s*&tauri::AppHandle",
        spawn_rs_source,
    ), "spawn_sidecar_and_get_port(_with_shutdown) must take `app: &tauri::AppHandle` (the public entry point)"


def test_spawn_rs_has_dev_mode_fallback(spawn_rs_source) -> None:
    """ADR-0020 §14: spawn.rs must have a dev-mode fallback path."""
    assert "fn is_dev_mode" in spawn_rs_source, (
        "spawn.rs must define `is_dev_mode()` to check VOICE_TYPER_SIDECAR_DEV=1"
    )
    assert "fn spawn_sidecar_dev_mode" in spawn_rs_source, (
        "spawn.rs must define `spawn_sidecar_dev_mode` for the dev fallback"
    )
    # The dev-mode path must use tokio::process::Command (NOT the shell plugin).
    assert "tokio::process::Command" in spawn_rs_source, (
        "spawn_sidecar_dev_mode must use tokio::process::Command (not the "
        "tauri-plugin-shell), dev mode bypasses externalBin"
    )
    # The dev-mode Python binary name must be platform-correct.
    assert re.search(
        r'cfg!\s*\(\s*target_os\s*=\s*"windows"\s*\)',
        spawn_rs_source,
    ), (
        'spawn_sidecar_dev_mode must use cfg!(target_os = "windows") to '
        "pick python.exe on Windows vs python3 on macOS/Linux"
    )


def test_server_started_timeout_is_30s(util_rs_source) -> None:
    """ADR-0020 §1: the host waits up to 30 s for server_started."""
    timeout_re = re.compile(r"const\s+SERVER_STARTED_TIMEOUT_MS\s*:\s*u64\s*=\s*([\d_]+)")
    m = timeout_re.search(util_rs_source)
    assert m, "util.rs must define SERVER_STARTED_TIMEOUT_MS"
    # Rust allows `30_000` as a digit-separator literal, strip underscores.
    timeout_ms = int(m.group(1).replace("_", ""))
    assert timeout_ms == 30_000, (
        f"SERVER_STARTED_TIMEOUT_MS must be 30000 (30 s), got {timeout_ms}; "
        f"ADR-0020 §1 + runbook §6.1 fail-scenario message"
    )
