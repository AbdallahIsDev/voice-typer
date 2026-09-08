//! Unit tests for `system_cmds::dialogs` (C-TEST-5 sibling file).
//!
//! Pins the `open_logs` target-directory contract: the command must
//! open `<config_dir>/logs/` — the exact directory
//! `platform::logging::init::init_file_logger` creates and rotates
//! `voice-typer-rust.log` in — NOT the config-dir root. The command
//! wrapper itself needs a live `tauri::Window` (which cannot be
//! constructed in unit tests) and an OS file-manager spawn, so the
//! assertable seam is the pure [`logs_dir_path`] helper — the same
//! pure-seam approach `platform::open_path` tests use with
//! `preflight_path_exists`.

use super::logs_dir_path;
use std::path::{Path, PathBuf};

#[test]
fn test_logs_dir_path_appends_logs_leaf_to_config_dir() {
    // The three platform-canonical config-dir shapes from
    // `platform::paths::config_dir_from_env` (Windows / macOS / Linux)
    // plus the legacy `~/.voice-typer` and the
    // VOICE_TYPER_CONFIG_DIR override — the helper must append the
    // `logs` leaf to whatever root it is given, unconditionally.
    let roots: [PathBuf; 5] = [
        // Windows: %APPDATA%/voice-typer
        PathBuf::from(r"C:\Users\alice\AppData\Roaming\voice-typer"),
        // macOS: ~/Library/Application Support/voice-typer
        PathBuf::from("/Users/alice/Library/Application Support/voice-typer"),
        // Linux: $XDG_DATA_HOME/voice-typer (default ~/.local/share/voice-typer)
        PathBuf::from("/home/alice/.local/share/voice-typer"),
        // Legacy Electron dir (still resolved when it exists)
        PathBuf::from("/home/alice/.voice-typer"),
        // VOICE_TYPER_CONFIG_DIR override
        PathBuf::from("/home/alice/custom-config"),
    ];
    for root in roots {
        let target = logs_dir_path(&root);
        assert_eq!(
            target,
            root.join("logs"),
            "open_logs target must be <config_dir>/logs"
        );
    }
}

#[test]
fn test_logs_dir_path_ends_with_logs_directory() {
    // The path handed to the OS file-manager opener must END with the
    // logs directory — this is the regression pin for the bug where
    // the command opened the config-dir ROOT instead of the logs
    // subdir (while the logger itself wrote into <config_dir>/logs,
    // so the user landed in a folder whose log files were one level
    // deeper).
    let target = logs_dir_path(Path::new("/home/alice/.local/share/voice-typer"));
    assert!(
        target.ends_with("logs"),
        "open_logs target must end with the logs directory, got {}",
        target.display()
    );
    // And it must NOT be the config-dir root itself.
    assert_ne!(
        target,
        PathBuf::from("/home/alice/.local/share/voice-typer"),
        "open_logs must not open the config-dir root"
    );
}

#[test]
fn test_logs_dir_path_matches_logging_init_layout() {
    // Byte-parity with `platform::logging::init::init_file_logger`,
    // which computes its own logs dir as
    // `config_dir.join("logs")` (init.rs) and writes
    // `voice-typer-rust.log` inside it. If either side changes the
    // leaf name, Open Logs would stop landing on the directory the
    // host actually writes logs into.
    let config_dir = Path::new("/home/alice/.local/share/voice-typer");
    let logging_init_dir = config_dir.join("logs");
    assert_eq!(
        logs_dir_path(config_dir),
        logging_init_dir,
        "open_logs target must match the logging init layout (<config_dir>/logs)"
    );
}
